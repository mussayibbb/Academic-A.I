import json
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest

from ai.llm.grounding import build_risk_summary_prompt, validate_risk_summary_selection
from ai.llm.provider import (
    DevelopmentDraftProvider,
    GeminiProvider,
    LocalLLMProvider,
    RiskSummaryContext,
    RiskSummaryEvidence,
)


def summary_context():
    return RiskSummaryContext(
        target_class='HIGH',
        risk_probability=0.82,
        evidence=[
            RiskSummaryEvidence(feature='attendance', value=61.0, contribution=0.21),
            RiskSummaryEvidence(feature='current_gpa', value=2.0, contribution=-0.12),
            RiskSummaryEvidence(feature='assessment', value=45.0, contribution=0.08),
        ],
    )


def test_risk_summary_prompt_and_selection_are_limited_to_verified_features():
    context = summary_context()
    prompt = build_risk_summary_prompt(context)

    assert '"target_class":"HIGH"' in prompt
    assert '"attendance"' in prompt
    assert 'not causes' in prompt
    assert 'student_name' not in prompt
    assert validate_risk_summary_selection('{"signals":["attendance","assessment"]}', context) == []
    assert validate_risk_summary_selection('{"signals":["unknown","attendance"]}', context)
    assert validate_risk_summary_selection('{"signals":["current_gpa"]}', context)
    assert validate_risk_summary_selection('not json', context)
    assert validate_risk_summary_selection('{"signals":["attendance"],"summary":"invented"}', context)


def test_ollama_generates_json_only_from_the_risk_summary_evidence(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return BytesIO(b'{"response":"{\\"signals\\":[\\"attendance\\",\\"current_gpa\\"]}"}')

    monkeypatch.setattr('ai.llm.provider.urlopen', fake_urlopen)
    provider = LocalLLMProvider('ollama:qwen3:8b', timeout_seconds=137)

    result = provider.generate_risk_summary(summary_context())

    assert json.loads(result) == {'signals': ['attendance', 'current_gpa']}
    assert len(calls) == 1
    assert calls[0][0].full_url.endswith('/api/generate')
    payload = json.loads(calls[0][0].data)
    assert payload['format'] == 'json'
    assert payload['options']['temperature'] == 0
    assert payload['think'] is False
    assert 'student_name' not in payload['prompt']
    assert calls[0][1] == 137


def test_ollama_risk_summary_respects_shorter_configured_timeout(monkeypatch):
    timeouts = []

    def fake_urlopen(request, timeout):
        timeouts.append(timeout)
        return BytesIO(b'{"response":"{\\"signals\\":[\\"attendance\\"]}"}')

    monkeypatch.setattr('ai.llm.provider.urlopen', fake_urlopen)
    provider = LocalLLMProvider('ollama:qwen3:8b', timeout_seconds=7)

    provider.generate_risk_summary(summary_context())

    assert timeouts == [7]


def test_ollama_risk_summary_reports_the_configured_timeout(monkeypatch):
    timeouts = []

    def fake_urlopen(request, timeout):
        timeouts.append(timeout)
        raise TimeoutError

    monkeypatch.setattr('ai.llm.provider.urlopen', fake_urlopen)
    provider = LocalLLMProvider('ollama:qwen3:8b', timeout_seconds=137)

    with pytest.raises(RuntimeError, match='generation timed out after 137 seconds'):
        provider.generate_risk_summary(summary_context())
    assert timeouts == [137]


def test_ollama_risk_summary_reports_an_unavailable_service(monkeypatch):
    def fake_urlopen(request, timeout):
        raise URLError('Connection refused')

    monkeypatch.setattr('ai.llm.provider.urlopen', fake_urlopen)
    provider = LocalLLMProvider('ollama:qwen3:8b', timeout_seconds=11)

    with pytest.raises(RuntimeError, match='Ollama service is not reachable'):
        provider.generate_risk_summary(summary_context())


def test_ollama_risk_summary_reports_a_missing_model(monkeypatch):
    def fake_urlopen(request, timeout):
        raise HTTPError(
            request.full_url,
            404,
            'Not Found',
            {},
            BytesIO(b'{"error":"model \\"qwen3:8b\\" not found, try pulling it first"}'),
        )

    monkeypatch.setattr('ai.llm.provider.urlopen', fake_urlopen)
    provider = LocalLLMProvider('ollama:qwen3:8b', timeout_seconds=11)

    with pytest.raises(RuntimeError, match='not installed.*ollama pull qwen3:8b'):
        provider.generate_risk_summary(summary_context())


def test_non_qwen_ollama_models_do_not_receive_qwen_thinking_option(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured['payload'] = json.loads(request.data)
        return BytesIO(b'{"response":"{\\"signals\\":[\\"attendance\\"]}"}')

    monkeypatch.setattr('ai.llm.provider.urlopen', fake_urlopen)
    provider = LocalLLMProvider('llama3.2:1b', timeout_seconds=11)

    provider.generate_risk_summary(summary_context())

    assert 'think' not in captured['payload']


def test_gemini_requests_structured_risk_summary_output(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured['request'] = request
        captured['timeout'] = timeout
        return BytesIO(b'{"candidates":[{"content":{"parts":[{"text":"{\\"signals\\":[\\"attendance\\"]}"}]}}]}')

    monkeypatch.setattr('ai.llm.provider.urlopen', fake_urlopen)
    provider = GeminiProvider('test-key', 'gemini-test')

    assert provider.generate_risk_summary(summary_context()) == '{"signals":["attendance"]}'
    payload = json.loads(captured['request'].data)
    assert payload['generationConfig']['responseMimeType'] == 'application/json'
    assert 'student_name' not in payload['contents'][0]['parts'][0]['text']
    assert captured['timeout'] == 15


def test_development_provider_does_not_claim_to_generate_an_ai_summary():
    with pytest.raises(RuntimeError, match='No language-model provider is configured'):
        DevelopmentDraftProvider().generate_risk_summary(summary_context())
