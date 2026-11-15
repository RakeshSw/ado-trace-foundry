"""Server-side Foundry calls protected by Upstash Redis limits. Never used in browser JS."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlsplit


class QuotaExceeded(RuntimeError):
    pass


# Atomic multi-key reservation: visitor-hour, global-day, in-flight counter.
# EVAL checks ALL counters before mutation. TTLs make reservations self-cleaning.
RESERVE_LUA = """
local u=tonumber(redis.call('GET',KEYS[1]) or '0')
local g=tonumber(redis.call('GET',KEYS[2]) or '0')
local c=tonumber(redis.call('GET',KEYS[3]) or '0')
if u>=tonumber(ARGV[1]) then return 'visitor-limit' end
if g>=tonumber(ARGV[2]) then return 'global-limit' end
if c>=tonumber(ARGV[3]) then return 'concurrency-limit' end
redis.call('INCR',KEYS[1]); redis.call('EXPIRE',KEYS[1],ARGV[4])
redis.call('INCR',KEYS[2]); redis.call('EXPIRE',KEYS[2],ARGV[5])
redis.call('INCR',KEYS[3]); redis.call('EXPIRE',KEYS[3],ARGV[6])
return 'allowed'
"""
RELEASE_LUA = """
local c=tonumber(redis.call('GET',KEYS[1]) or '0')
if c>0 then return redis.call('DECR',KEYS[1]) else return 0 end
"""


def _limit(name: str, default: int) -> int:
    # Limits are server-only; explicitly reject invalid or zero configuration.
    val = int(os.environ.get(name, str(default)))
    if not 1 <= val <= 100000:
        raise ValueError(f'{name} must be a positive integer')
    return val


class UpstashLimiter:
    def __init__(self, transport=None):
        self.url = os.environ.get('UPSTASH_REDIS_REST_URL', '').rstrip('/')
        self.token = os.environ.get('UPSTASH_REDIS_REST_TOKEN', '')
        if not self.url or not self.token:
            raise RuntimeError('AI disabled: Upstash Redis credentials are missing (fail closed)')
        parsed = urlsplit(self.url)
        if parsed.scheme != 'https' or not parsed.hostname or not parsed.hostname.endswith('.upstash.io') or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Expected HTTPS Upstash Redis REST endpoint')
        self.transport = transport
        self.per_hour = _limit('AI_RATE_LIMIT_PER_HOUR', 5)
        self.global_day = _limit('AI_GLOBAL_LIMIT_PER_DAY', 50)
        self.concurrent = _limit('AI_MAX_CONCURRENT_REQUESTS', 1)

    def command(self, args: list):
        req = urllib.request.Request(self.url,
             data=json.dumps(args).encode(), method='POST',
             headers={'Authorization': f'Bearer {self.token}', 'Content-Type': 'application/json'})
        try:
            if self.transport:
                response = self.transport(req)
            else:
                with urllib.request.urlopen(req, timeout=8) as r:
                    response = json.load(r)
        except Exception as exc:
            raise RuntimeError('AI disabled: Redis rate limiter unavailable') from exc
        if not isinstance(response, dict) or response.get('error') is not None or 'result' not in response:
            raise RuntimeError('AI disabled: invalid Redis rate limiter response')
        return response['result']

    def reserve(self, visitor_id: str):
        if not re.fullmatch(r'[a-f0-9]{32}', visitor_id):
            raise ValueError('Invalid server-generated visitor ID')
        now = datetime.now(timezone.utc)
        hour = now.strftime('%Y%m%d%H')
        day = now.strftime('%Y%m%d')
        keys = [f'adotrace:v04:visitor:{visitor_id}:{hour}',
                f'adotrace:v04:global:{day}', 'adotrace:v04:inflight']
        # time-based TTL with grace; key boundary uses UTC clock (not sliding window).
        ttl_hour = 3700 - now.minute * 60 - now.second
        ttl_day = 90000 - (now.hour * 3600 + now.minute * 60 + now.second)
        result = self.command(['EVAL', RESERVE_LUA, 3, *keys,
                  self.per_hour, self.global_day, self.concurrent, ttl_hour, ttl_day, 180])
        if result != 'allowed':
            raise QuotaExceeded(f'AI request denied: {result}')
        return keys[2]

    def release(self, inflight_key):
        self.command(['EVAL', RELEASE_LUA, 1, inflight_key])


def _get_azure_url():
    endpoint = os.environ.get('AZURE_OPENAI_ENDPOINT', '').rstrip('/')
    deployment = os.environ.get('AZURE_OPENAI_DEPLOYMENT', '')
    key = os.environ.get('AZURE_OPENAI_API_KEY', '')
    p = urlsplit(endpoint)
    if not p.hostname or p.scheme != 'https' or not p.hostname.endswith(('.openai.azure.com', '.services.ai.azure.com', '.cognitiveservices.azure.com')) or p.path not in ('', '/', '/openai/v1') or p.query or p.fragment:
        raise RuntimeError('Invalid Azure AI HTTPS endpoint')
    if not deployment or not key:
        raise RuntimeError('Azure Foundry server-side credentials missing')
    return endpoint.removesuffix('/openai/v1') + '/openai/v1/responses', deployment, key


def _extract_response_text(obj):
    if obj.get('status') not in (None, 'completed'):
        raise RuntimeError('Foundry did not complete request')
    chunks = [part['text'] for item in obj.get('output', [])
              for part in item.get('content', [])
              if part.get('type') == 'output_text' and isinstance(part.get('text'), str)]
    if not chunks:
        raise ValueError('Foundry returned no text')
    return '\n'.join(chunks)


def _parse_json(text):
    text = text.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.I).strip()
    return json.loads(text)


def call_foundry(input_data: dict, instructions: str, *, transport=None) -> dict:
    url, model, key = _get_azure_url()
    payload = {'model': model, 'instructions': instructions,
               'input': json.dumps(input_data, ensure_ascii=False)}
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), method='POST',
                                     headers={'api-key': key, 'Content-Type': 'application/json'})
    try:
        if transport:
            response = transport(request)
        else:
            with urllib.request.urlopen(request, timeout=90) as r:
                response = json.load(r)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'Azure Foundry request failed: HTTP {exc.code}') from exc
    result = _parse_json(_extract_response_text(response))
    if not isinstance(result, dict):
        raise ValueError('Expected JSON object from model')
    return result


def _story_text(value, *, field: str) -> str:
    """Normalize common LLM JSON shapes without inventing missing story content."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                chunk = item.strip()
            elif isinstance(item, dict):
                # Prefer testable criterion statements, not arbitrary dict serialization.
                chunk = next((item[key].strip() for key in
                              ('criterion', 'text', 'description', 'statement', 'given_when_then')
                              if isinstance(item.get(key), str) and item[key].strip()), '')
                if not chunk and all(isinstance(item.get(key), str) for key in ('given', 'when', 'then')):
                    chunk = f"Given {item['given'].strip()}, when {item['when'].strip()}, then {item['then'].strip()}"
            else:
                raise ValueError(f'Invalid AI-generated {field}: unsupported list element')
            if not chunk:
                raise ValueError(f'Invalid AI-generated {field}: empty list element')
            parts.append(chunk)
        if not parts:
            return ''
        return '\n'.join(f'{i}. {part}' for i, part in enumerate(parts, 1))
    if isinstance(value, dict):
        for key in ('text', 'description', 'statement'):
            if isinstance(value.get(key), str):
                return value[key].strip()
    raise ValueError(f'Invalid AI-generated {field}: unsupported field type')


def generate_story(feature: dict, *, visitor_id: str, limiter=None, transport=None) -> dict:
    """Only the feature context is fixed; story content is generated by the model."""
    limiter = limiter or UpstashLimiter()
    lock_key = limiter.reserve(visitor_id)
    try:
        context = {'feature_id': feature['id'], 'feature_title': feature['title'],
                   'feature_description': str(feature.get('fields', {}).get('System.Description', ''))[:3000]}
        instruction = (
            'You are generating a synthetic Agile User Story for a SampleCommerce training project. '
            'Never use real names, secrets, personal data or proprietary systems. '
            'Create a fresh, realistic story relevant to the feature context, not a fixed example. '
            'Return ONLY JSON keys: title, description, acceptance_criteria. '
            'description must use As a / I want / So that and explain behavior; '
            'acceptance_criteria must contain at least 3 independently testable criteria with concrete outcomes. '
            'Do not claim anything is already implemented.')
        data = call_foundry(context, instruction, transport=transport)
        # The model sometimes returns acceptance criteria as JSON arrays.
        # Normalize supported shapes, but do not fabricate missing requirements.
        result = {}
        for field in ('title', 'description', 'acceptance_criteria'):
            raw = data.get(field)
            if field == 'acceptance_criteria' and raw is None:
                raw = data.get('acceptanceCriteria', data.get('criteria'))
            if field == 'title' and not isinstance(raw, str):
                raise ValueError('Invalid AI-generated title: must be a string')
            value = _story_text(raw, field=field)
            if not value or len(value) > 8000:
                raise ValueError(f'Invalid AI-generated {field}: empty or too long')
            result[field] = value
        return result
    finally:
        limiter.release(lock_key)
