import json
import time
from typing import Any, Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field

from ai.llm.grounding import (
    build_intervention_prompt,
    build_recovery_tutor_prompt,
    build_recovery_blueprint_prompt,
    build_risk_summary_prompt,
    build_short_answer_evaluation_prompt,
    build_topic_assessment_prompt,
    build_topic_map_prompt,
)


REVIEW_HEADER = 'AI-GENERATED DRAFT - FACULTY REVIEW REQUIRED'
RISK_SUMMARY_TIMEOUT_SECONDS = 15


class InterventionContext(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    student_name: str
    risk_level: str
    risk_probability: float
    class_probabilities: dict[str, float] = Field(default_factory=dict)
    model_version: str = ''
    shap_factors: list[str] = Field(default_factory=list)
    shap_evidence: list[dict[str, Any]] = Field(default_factory=list)
    momentum: dict[str, str] = Field(default_factory=dict)
    temporal_evidence: list[str] = Field(default_factory=list)
    causal_evidence: list[str] = Field(default_factory=list)
    attendance: float
    assessment: float
    assignment_engagement: float
    institutional_resources: list[dict[str, str]] = Field(default_factory=list)
    subject_academic_context: dict[str, dict[str, Any]] = Field(default_factory=dict)
    source_notice: str = 'No matching institutional source was found; do not state institutional policy.'


class RiskSummaryEvidence(BaseModel):
    feature: str
    value: float = Field(allow_inf_nan=False)
    contribution: float = Field(allow_inf_nan=False)


class RiskSummaryContext(BaseModel):
    target_class: Literal['HIGH', 'MEDIUM', 'LOW']
    risk_probability: float = Field(ge=0, le=1, allow_inf_nan=False)
    evidence: list[RiskSummaryEvidence] = Field(min_length=1, max_length=5)


class RecoveryTutorContext(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    academic_evidence: list[dict[str, Any]] = Field(max_length=40)
    missing_information: list[str] = Field(max_length=20)
    conversation_history: list[dict[str, str]] = Field(default_factory=list, max_length=6)


class LLMProvider(Protocol):
    provider_name: str

    def generate_intervention(self, context: InterventionContext) -> str: ...

    def generate_risk_summary(self, context: RiskSummaryContext) -> str: ...

    def generate_recovery_tutor(self, context: RecoveryTutorContext) -> str: ...

    def generate_topic_assessment(self, context: dict[str, Any]) -> str: ...

    def generate_topic_map(self, context: dict[str, Any]) -> str: ...

    def generate_recovery_blueprint(self, context: dict[str, Any]) -> str: ...

    def evaluate_short_answer(self, context: dict[str, Any]) -> str: ...


class LocalLLMProvider:
    provider_name = 'ollama-local'

    def __init__(self, model_name: str, base_url: str = 'http://127.0.0.1:11434', timeout_seconds: int = 180, health_check_retries: int = 2) -> None:
        self.model_name = model_name.strip().removeprefix('ollama:').strip()
        self.base_url = (base_url or 'http://127.0.0.1:11434').rstrip('/')
        self.timeout_seconds = timeout_seconds
        self.health_check_retries = max(1, health_check_retries)

    def _request_json(self, endpoint: str, *, method: str = 'GET', payload: bytes | None = None, timeout: int | None = None) -> dict[str, Any]:
        request = Request(f'{self.base_url}{endpoint}', data=payload, headers={'Content-Type': 'application/json'} if payload is not None else {}, method=method)
        with urlopen(request, timeout=timeout or self.timeout_seconds) as response:
            return json.loads(response.read().decode('utf-8'))

    def get_health_status(self) -> dict[str, Any]:
        status = {'provider': self.provider_name, 'base_url': self.base_url, 'model': self.model_name, 'reachable': False, 'model_available': False, 'status': 'unavailable'}
        for attempt in range(self.health_check_retries + 1):
            try:
                result = self._request_json('/api/tags', timeout=min(self.timeout_seconds, 10))
                models = result.get('models', []) if isinstance(result, dict) else []
                available_names = []
                for item in models:
                    if isinstance(item, dict):
                        available_names.append(str(item.get('name') or item.get('model') or '').strip())
                status['reachable'] = True
                status['model_available'] = self.model_name in available_names
                status['status'] = 'ready' if status['model_available'] else 'model_missing'
                status['error'] = None if status['model_available'] else f'Ollama is reachable at {self.base_url}, but the required model "{self.model_name}" is not available. Install it with `ollama pull {self.model_name}`.'
                return status
            except HTTPError as exc:
                status['status'] = 'http_error'
                status['error'] = f'Ollama HTTP error at {self.base_url}/api/tags: {exc.code} {exc.reason}.'
                return status
            except (URLError, TimeoutError, OSError, ConnectionError) as exc:
                if attempt < self.health_check_retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                status['status'] = 'unavailable'
                status['error'] = f'Ollama service is not reachable at {self.base_url}. Start the Ollama service and retry.'
                return status
        return status

    def generate_intervention(self, context: InterventionContext) -> str:
        health = self.get_health_status()
        if not health['reachable']:
            raise RuntimeError(health.get('error') or f'Ollama service is not reachable at {self.base_url}. Start the Ollama service and retry.')
        if not health['model_available']:
            raise RuntimeError(health.get('error') or f'Ollama is reachable at {self.base_url}, but the required model "{self.model_name}" is not installed.')

        prompt = build_intervention_prompt(context)
        request_payload: dict[str, Any] = {'model': self.model_name, 'prompt': prompt, 'stream': False}
        if self.model_name.casefold().startswith('qwen3'):
            request_payload['think'] = False
        body = json.dumps(request_payload).encode('utf-8')
        try:
            result = self._request_json('/api/generate', method='POST', payload=body, timeout=self.timeout_seconds)
        except TimeoutError as exc:
            raise RuntimeError(f'Ollama generation timed out after {self.timeout_seconds} seconds while generating with the "{self.model_name}" model. Please retry or increase LLM_TIMEOUT_SECONDS.') from exc
        except HTTPError as exc:
            raise RuntimeError(f'Ollama generation request failed with HTTP {exc.code} at {self.base_url}/api/generate: {exc.reason}.') from exc
        except (URLError, OSError, ConnectionError) as exc:
            raise RuntimeError(f'Ollama service is not reachable at {self.base_url}. Start the Ollama service and retry.') from exc
        generated = str(result.get('response', '')).strip()
        if not generated:
            raise RuntimeError('Ollama returned an empty response.')
        return f'{REVIEW_HEADER}\n{generated}'

    def generate_risk_summary(self, context: RiskSummaryContext) -> str:
        prompt = build_risk_summary_prompt(context)
        request_payload: dict[str, Any] = {
            'model': self.model_name,
            'prompt': prompt,
            'format': 'json',
            'options': {'temperature': 0},
            'stream': False,
        }
        if self.model_name.casefold().startswith('qwen3'):
            request_payload['think'] = False
        body = json.dumps(request_payload).encode('utf-8')
        try:
            result = self._request_json('/api/generate', method='POST', payload=body, timeout=self.timeout_seconds)
        except json.JSONDecodeError as exc:
            raise RuntimeError('Ollama returned an invalid risk-summary response.') from exc
        except TimeoutError as exc:
            raise RuntimeError(f'Ollama risk-summary generation timed out after {self.timeout_seconds} seconds. Please retry or increase LLM_TIMEOUT_SECONDS.') from exc
        except HTTPError as exc:
            if exc.code == 404:
                try:
                    error_body = json.loads(exc.read().decode('utf-8'))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    error_body = {}
                detail = error_body.get('error') if isinstance(error_body, dict) else None
                if isinstance(detail, str) and 'model' in detail.casefold() and 'not found' in detail.casefold():
                    raise RuntimeError(f'Ollama model "{self.model_name}" is not installed. Install it with `ollama pull {self.model_name}`.') from exc
            raise RuntimeError(f'Ollama risk-summary request failed with HTTP {exc.code} at {self.base_url}/api/generate: {exc.reason}.') from exc
        except URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise RuntimeError(f'Ollama risk-summary generation timed out after {self.timeout_seconds} seconds. Please retry or increase LLM_TIMEOUT_SECONDS.') from exc
            raise RuntimeError(f'Ollama service is not reachable at {self.base_url}. Start the Ollama service and retry.') from exc
        except (OSError, ConnectionError) as exc:
            raise RuntimeError(f'Ollama service is not reachable at {self.base_url}. Start the Ollama service and retry.') from exc
        if not isinstance(result, dict):
            raise RuntimeError('Ollama returned a non-object risk-summary response.')
        generated = str(result.get('response', '')).strip()
        if not generated:
            raise RuntimeError('Ollama returned an empty risk-summary selection.')
        return generated

    def generate_recovery_tutor(self, context: RecoveryTutorContext) -> str:
        health = self.get_health_status()
        if not health['reachable']:
            raise RuntimeError(health.get('error') or f'Ollama service is not reachable at {self.base_url}.')
        if not health['model_available']:
            raise RuntimeError(health.get('error') or f'Ollama model "{self.model_name}" is not available.')

        prompt = build_recovery_tutor_prompt(context)
        body = json.dumps({
            'model': self.model_name,
            'prompt': prompt,
            'format': 'json',
            'options': {'temperature': 0},
            'stream': False,
        }).encode('utf-8')
        try:
            result = self._request_json('/api/generate', method='POST', payload=body, timeout=self.timeout_seconds)
        except json.JSONDecodeError as exc:
            raise RuntimeError('Ollama returned an invalid recovery-tutor response.') from exc
        except TimeoutError as exc:
            raise RuntimeError(f'Ollama recovery-tutor generation timed out after {self.timeout_seconds} seconds.') from exc
        except HTTPError as exc:
            raise RuntimeError(f'Ollama recovery-tutor request failed with HTTP {exc.code}.') from exc
        except (URLError, OSError, ConnectionError) as exc:
            raise RuntimeError(f'Ollama service is unavailable at {self.base_url}.') from exc
        generated = result.get('response') if isinstance(result, dict) else None
        if not isinstance(generated, str) or not generated.strip():
            raise RuntimeError('Ollama returned an empty or malformed recovery-tutor response.')
        return generated.strip()

    def generate_topic_assessment(self, context: dict[str, Any]) -> str:
        health = self.get_health_status()
        if not health['reachable'] or not health['model_available']:
            raise RuntimeError(health.get('error') or 'Configured Ollama model is unavailable.')
        payload = json.dumps({
            'model': self.model_name,
            'prompt': build_topic_assessment_prompt(context),
            'format': 'json',
            'options': {'temperature': 0},
            'stream': False,
        }).encode('utf-8')
        try:
            result = self._request_json('/api/generate', method='POST', payload=payload, timeout=self.timeout_seconds)
        except (HTTPError, URLError, TimeoutError, OSError, ConnectionError, json.JSONDecodeError) as exc:
            raise RuntimeError(f'Ollama assessment generation failed: {exc}') from exc
        generated = result.get('response') if isinstance(result, dict) else None
        if not isinstance(generated, str) or not generated.strip():
            raise RuntimeError('Ollama returned an empty assessment response.')
        return generated.strip()

    def generate_topic_map(self, context: dict[str, Any]) -> str:
        health = self.get_health_status()
        if not health['reachable'] or not health['model_available']:
            raise RuntimeError(health.get('error') or 'Configured Ollama model is unavailable.')
        payload = json.dumps({
            'model': self.model_name,
            'prompt': build_topic_map_prompt(context),
            'format': 'json',
            'options': {'temperature': 0},
            'stream': False,
        }).encode('utf-8')
        try:
            result = self._request_json('/api/generate', method='POST', payload=payload, timeout=self.timeout_seconds)
        except (HTTPError, URLError, TimeoutError, OSError, ConnectionError, json.JSONDecodeError) as exc:
            raise RuntimeError(f'Ollama topic extraction failed: {exc}') from exc
        generated = result.get('response') if isinstance(result, dict) else None
        if not isinstance(generated, str) or not generated.strip():
            raise RuntimeError('Ollama returned an empty topic-map response.')
        return generated.strip()

    def generate_recovery_blueprint(self, context: dict[str, Any]) -> str:
        health = self.get_health_status()
        if not health['reachable'] or not health['model_available']:
            raise RuntimeError(health.get('error') or 'Configured Ollama model is unavailable.')
        payload = json.dumps({
            'model': self.model_name,
            'prompt': build_recovery_blueprint_prompt(context),
            'format': 'json',
            'options': {'temperature': 0},
            'stream': False,
        }).encode('utf-8')
        try:
            result = self._request_json('/api/generate', method='POST', payload=payload, timeout=self.timeout_seconds)
        except (HTTPError, URLError, TimeoutError, OSError, ConnectionError, json.JSONDecodeError) as exc:
            raise RuntimeError(f'Ollama recovery blueprint generation failed: {exc}') from exc
        generated = result.get('response') if isinstance(result, dict) else None
        if not isinstance(generated, str) or not generated.strip():
            raise RuntimeError('Ollama returned an empty recovery blueprint response.')
        return generated.strip()

    def evaluate_short_answer(self, context: dict[str, Any]) -> str:
        health = self.get_health_status()
        if not health['reachable'] or not health['model_available']:
            raise RuntimeError(health.get('error') or 'Configured Ollama model is unavailable.')
        payload = json.dumps({
            'model': self.model_name,
            'prompt': build_short_answer_evaluation_prompt(context),
            'format': 'json',
            'options': {'temperature': 0},
            'stream': False,
        }).encode('utf-8')
        try:
            result = self._request_json('/api/generate', method='POST', payload=payload, timeout=self.timeout_seconds)
        except (HTTPError, URLError, TimeoutError, OSError, ConnectionError, json.JSONDecodeError) as exc:
            raise RuntimeError(f'Ollama short-answer evaluation failed: {exc}') from exc
        generated = result.get('response') if isinstance(result, dict) else None
        if not isinstance(generated, str) or not generated.strip():
            raise RuntimeError('Ollama returned an empty short-answer evaluation.')
        return generated.strip()


class GeminiProvider:
    provider_name = 'gemini'

    def __init__(self, api_key: str, model_name: str, timeout_seconds: int = 60) -> None:
        if not api_key.strip():
            raise ValueError('GEMINI_API_KEY is required when Gemini is selected.')
        if not model_name.strip():
            raise ValueError('GEMINI_MODEL_NAME must name an available Gemini model.')
        self.api_key = api_key.strip()
        self.model_name = model_name.strip()
        self.timeout_seconds = timeout_seconds

    def generate_intervention(self, context: InterventionContext) -> str:
        prompt = build_intervention_prompt(context)
        payload = json.dumps({'contents': [{'parts': [{'text': prompt}]}]}).encode('utf-8')
        request = Request(
            f'https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent',
            data=payload,
            headers={'Content-Type': 'application/json', 'x-goog-api-key': self.api_key},
            method='POST',
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                result = json.loads(response.read().decode('utf-8'))
        except HTTPError as exc:
            raise RuntimeError(f'Gemini request failed with HTTP {exc.code}.') from exc
        except (URLError, TimeoutError) as exc:
            raise RuntimeError(f'Gemini is unavailable: {exc}') from exc
        candidates = result.get('candidates') or []
        parts = candidates[0].get('content', {}).get('parts', []) if candidates else []
        generated = '\n'.join(str(part.get('text', '')).strip() for part in parts if isinstance(part, dict) and part.get('text')).strip()
        if not generated:
            raise RuntimeError('Gemini returned an empty response')
        return f'{REVIEW_HEADER}\n{generated}'

    def generate_risk_summary(self, context: RiskSummaryContext) -> str:
        prompt = build_risk_summary_prompt(context)
        payload = json.dumps({
            'contents': [{'parts': [{'text': prompt}]}],
            'generationConfig': {'responseMimeType': 'application/json', 'temperature': 0},
        }).encode('utf-8')
        request = Request(
            f'https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent',
            data=payload,
            headers={'Content-Type': 'application/json', 'x-goog-api-key': self.api_key},
            method='POST',
        )
        timeout = min(self.timeout_seconds, RISK_SUMMARY_TIMEOUT_SECONDS)
        try:
            with urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read().decode('utf-8'))
        except json.JSONDecodeError as exc:
            raise RuntimeError('Gemini returned an invalid risk-summary response.') from exc
        except HTTPError as exc:
            raise RuntimeError(f'Gemini risk-summary request failed with HTTP {exc.code}.') from exc
        except (URLError, TimeoutError) as exc:
            raise RuntimeError(f'Gemini risk-summary service is unavailable: {exc}') from exc
        if not isinstance(result, dict):
            raise RuntimeError('Gemini returned a non-object risk-summary response.')
        candidates = result.get('candidates')
        first_candidate = candidates[0] if isinstance(candidates, list) and candidates else {}
        content = first_candidate.get('content') if isinstance(first_candidate, dict) else None
        parts = content.get('parts', []) if isinstance(content, dict) else []
        if not isinstance(parts, list):
            raise RuntimeError('Gemini returned an invalid risk-summary response structure.')
        generated = '\n'.join(str(part.get('text', '')).strip() for part in parts if isinstance(part, dict) and part.get('text')).strip()
        if not generated:
            raise RuntimeError('Gemini returned an empty risk-summary selection.')
        return generated

    def generate_recovery_tutor(self, context: RecoveryTutorContext) -> str:
        prompt = build_recovery_tutor_prompt(context)
        payload = json.dumps({
            'contents': [{'parts': [{'text': prompt}]}],
            'generationConfig': {'responseMimeType': 'application/json', 'temperature': 0},
        }).encode('utf-8')
        request = Request(
            f'https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent',
            data=payload,
            headers={'Content-Type': 'application/json', 'x-goog-api-key': self.api_key},
            method='POST',
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                result = json.loads(response.read().decode('utf-8'))
        except json.JSONDecodeError as exc:
            raise RuntimeError('Gemini returned an invalid recovery-tutor response.') from exc
        except HTTPError as exc:
            raise RuntimeError(f'Gemini recovery-tutor request failed with HTTP {exc.code}.') from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f'Gemini recovery-tutor service is unavailable: {exc}') from exc
        if not isinstance(result, dict):
            raise RuntimeError('Gemini returned a malformed recovery-tutor response.')
        candidates = result.get('candidates')
        first_candidate = candidates[0] if isinstance(candidates, list) and candidates else {}
        content = first_candidate.get('content') if isinstance(first_candidate, dict) else None
        parts = content.get('parts', []) if isinstance(content, dict) else []
        generated = '\n'.join(
            str(part.get('text', '')).strip()
            for part in parts
            if isinstance(part, dict) and isinstance(part.get('text'), str)
        ).strip() if isinstance(parts, list) else ''
        if not generated:
            raise RuntimeError('Gemini returned an empty recovery-tutor response.')
        return generated

    def _generate_structured(self, prompt: str, operation: str) -> str:
        payload = json.dumps({
            'contents': [{'parts': [{'text': prompt}]}],
            'generationConfig': {'responseMimeType': 'application/json', 'temperature': 0},
        }).encode('utf-8')
        request = Request(
            f'https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent',
            data=payload,
            headers={'Content-Type': 'application/json', 'x-goog-api-key': self.api_key},
            method='POST',
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                result = json.loads(response.read().decode('utf-8'))
        except (json.JSONDecodeError, HTTPError, URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f'Gemini {operation} failed: {exc}') from exc
        candidates = result.get('candidates') if isinstance(result, dict) else None
        first = candidates[0] if isinstance(candidates, list) and candidates else {}
        content = first.get('content') if isinstance(first, dict) else None
        parts = content.get('parts', []) if isinstance(content, dict) else []
        generated = '\n'.join(
            part['text'].strip() for part in parts
            if isinstance(part, dict) and isinstance(part.get('text'), str)
        ).strip() if isinstance(parts, list) else ''
        if not generated:
            raise RuntimeError(f'Gemini returned an empty {operation} response.')
        return generated

    def generate_topic_assessment(self, context: dict[str, Any]) -> str:
        return self._generate_structured(build_topic_assessment_prompt(context), 'assessment generation')

    def generate_topic_map(self, context: dict[str, Any]) -> str:
        return self._generate_structured(build_topic_map_prompt(context), 'topic extraction')

    def generate_recovery_blueprint(self, context: dict[str, Any]) -> str:
        return self._generate_structured(build_recovery_blueprint_prompt(context), 'recovery blueprint generation')

    def evaluate_short_answer(self, context: dict[str, Any]) -> str:
        return self._generate_structured(build_short_answer_evaluation_prompt(context), 'short-answer evaluation')


def build_provider(settings) -> LLMProvider:
    selected = getattr(settings, 'selected_llm_provider', 'ollama')
    llm_model_name = getattr(settings, 'llm_model_name', '') or ''
    model_name = llm_model_name.strip()
    base_url = getattr(settings, 'ollama_base_url', None) or getattr(settings, 'llm_base_url', None) or 'http://127.0.0.1:11434'

    if selected == 'gemini':
        if settings.gemini_api_key:
            return GeminiProvider(settings.gemini_api_key, settings.gemini_model_name, settings.llm_timeout_seconds)
        if model_name.lower().startswith('ollama:') or model_name.startswith('qwen'):
            return LocalLLMProvider(model_name, base_url=base_url, timeout_seconds=settings.llm_timeout_seconds)
        return DevelopmentDraftProvider()
    if model_name.lower().startswith('ollama:') or model_name.startswith('qwen'):
        return LocalLLMProvider(model_name, base_url=base_url, timeout_seconds=settings.llm_timeout_seconds)
    return DevelopmentDraftProvider()


class DevelopmentDraftProvider:
    provider_name = 'development-template-not-an-llm'

    def generate_intervention(self, context: InterventionContext) -> str:
        top_factors = ', '.join(item.get('feature', '') for item in context.shap_evidence[:2] if item.get('feature'))
        trend = ', '.join(f'{name}: {value.lower()}' for name, value in context.momentum.items()) or 'Not enough verified evidence.'
        resource_line = ' '.join(item.get('title', '') for item in context.institutional_resources if item.get('title'))
        evidence = f'Current attendance is {context.attendance:.0f}% and assessment performance is {context.assessment:.0f}%. '
        sources = f' Relevant indexed resources: {resource_line}.' if resource_line else f' {context.source_notice}'
        return (
            f'{REVIEW_HEADER}\n'
            f'Subject: Support check-in\nHi {context.student_name}, I would like to check in about your academic progress. '
            'OBSERVATIONS:\n'
            f'Current risk is {context.risk_level} ({context.risk_probability:.3f}). '
            f'Verified model factors for review: {top_factors or "Not enough verified evidence."}. '
            f'Observed momentum: {trend}. {evidence}'
            f'RECOMMENDATIONS:\nConsider discussing one practical next step together with the student.{sources}\n'
            '[Development template; this was not generated by a language model.]'
        )

    def generate_risk_summary(self, context: RiskSummaryContext) -> str:
        raise RuntimeError(f'No language-model provider is configured to summarize P({context.target_class}).')

    def generate_recovery_tutor(self, context: RecoveryTutorContext) -> str:
        raise RuntimeError('No language-model provider is configured for the recovery tutor.')

    def generate_topic_assessment(self, context: dict[str, Any]) -> str:
        raise RuntimeError('No language-model provider is configured for assessment generation.')

    def generate_topic_map(self, context: dict[str, Any]) -> str:
        raise RuntimeError('No language-model provider is configured for topic extraction.')

    def generate_recovery_blueprint(self, context: dict[str, Any]) -> str:
        raise RuntimeError('No language-model provider is configured for recovery blueprint generation.')

    def evaluate_short_answer(self, context: dict[str, Any]) -> str:
        raise RuntimeError('No language-model provider is configured for short-answer evaluation.')