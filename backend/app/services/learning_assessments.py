from __future__ import annotations

import json
import re
from typing import Any

_TOPIC_FIELDS = {
    'source_order', 'original_number', 'title', 'summary', 'learning_objectives',
    'concepts', 'source_excerpt', 'source_locator', 'order_uncertain',
    'hierarchy_uncertain', 'requires_review', 'review_reason', 'children',
}
_MAX_TOPIC_COUNT = 100
_MAX_TOPIC_DEPTH = 5


def _normalized(value: str) -> str:
    return re.sub(r'\s+', ' ', value).strip().casefold()


def _content_words(value: str) -> set[str]:
    stop_words = {'about', 'after', 'again', 'also', 'among', 'does', 'from', 'into', 'more', 'most', 'only', 'other', 'over', 'same', 'some', 'than', 'that', 'their', 'them', 'then', 'there', 'these', 'they', 'this', 'those', 'through', 'under', 'very', 'what', 'when', 'where', 'which', 'while', 'with', 'would', 'could', 'should', 'question', 'statement', 'according', 'source', 'passage', 'describe', 'explain', 'identify'}
    return {word for word in re.findall(r'\b[a-z]{3,}\b', value.casefold()) if word not in stop_words}


def _source_segment(
    source_text: str,
    locator: dict[str, Any] | None,
    allowed_locator_types: set[str] | None = None,
) -> str | None:
    if locator is None:
        return source_text
    kind = locator.get('type')
    value = locator.get('value')
    if (
        kind not in {'page', 'slide'}
        or (allowed_locator_types is not None and kind not in allowed_locator_types)
        or isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
    ):
        return None
    marker_name = {'page': 'PAGE', 'slide': 'SLIDE'}[kind]
    marker = re.compile(rf'(?m)^\[\[\[SOURCE_{marker_name}:{value}\]\]\]\s*$')
    match = marker.search(source_text)
    if not match:
        return None
    following = re.search(r'(?m)^\[\[\[SOURCE_(?:PAGE|SLIDE):\d+\]\]\]\s*$', source_text[match.end():])
    end = match.end() + following.start() if following else len(source_text)
    return source_text[match.end():end].strip()


def validate_topic_map_response(
    raw: str,
    source_text: str,
    context: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str], str | None]:
    try:
        result = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return [], ['The topic extractor response was not valid JSON.'], None
    required_response_fields = {
        'course_title', 'course_title_evidence', 'module_number', 'module_title',
        'topics', 'limitation',
    }
    if not isinstance(result, dict) or set(result) != required_response_fields:
        return [], ['The topic extractor response did not match the required syllabus-map structure.'], None
    topics = result.get('topics')
    limitation = result.get('limitation')
    if not isinstance(topics, list) or len(topics) > _MAX_TOPIC_COUNT:
        return [], [f'The topic map must contain no more than {_MAX_TOPIC_COUNT} top-level topics.'], limitation if isinstance(limitation, str) else None
    issues: list[str] = []
    validated: list[dict[str, Any]] = []
    expected_module = (context or {}).get('module', {})
    source_context = (context or {}).get('source', {})
    if 'file_type' in source_context:
        allowed_locator_types = {
            'PDF': {'page'},
            'PPTX': {'slide'},
        }.get(str(source_context.get('file_type', '')).upper(), set())
    else:
        allowed_locator_types = {'page', 'slide'}
    course_title = result.get('course_title')
    course_evidence = result.get('course_title_evidence')
    if course_title is not None:
        if not isinstance(course_title, str) or not course_title.strip() or len(course_title) > 200:
            issues.append('Course title must be null or a non-empty string of at most 200 characters.')
            course_title, course_evidence = None, None
        elif not isinstance(course_evidence, str) or _normalized(course_evidence) not in _normalized(source_text) or _normalized(course_title) not in _normalized(course_evidence):
            issues.append('Course title is not supported by its exact source evidence.')
            course_title, course_evidence = None, None
    elif course_evidence is not None:
        issues.append('Course-title evidence must be null when the course title is unknown.')
        course_evidence = None
    module_number = result.get('module_number')
    module_title = result.get('module_title')
    if (
        isinstance(module_number, bool)
        or not isinstance(module_number, int)
        or module_number != expected_module.get('number')
        or not isinstance(module_title, str)
        or module_title != expected_module.get('title')
    ):
        issues.append('Returned module metadata did not match the teacher-selected module.')

    seen_candidates: set[tuple[str, str, str]] = set()
    flattened_order = 0
    candidate_count = 0

    def validate_topic_list(items: list[Any], parent_number: str | None, path: tuple[int, ...], depth: int) -> list[dict[str, Any]]:
        nonlocal flattened_order, candidate_count
        output: list[dict[str, Any]] = []
        if depth > _MAX_TOPIC_DEPTH:
            issues.append(f'Topic hierarchy exceeds the maximum depth of {_MAX_TOPIC_DEPTH}.')
            return output
        for sibling_index, topic in enumerate(items, start=1):
            candidate_count += 1
            flattened_order += 1
            order = flattened_order
            if candidate_count > _MAX_TOPIC_COUNT:
                issues.append(f'Topic map exceeds the maximum of {_MAX_TOPIC_COUNT} total candidates.')
                return output
            candidate_path = (*path, sibling_index)
            if not isinstance(topic, dict) or set(topic) != _TOPIC_FIELDS:
                issues.append(f'Topic {order} did not match the required topic structure and was omitted.')
                continue
            title = topic.get('title')
            if not isinstance(title, str) or not title.strip() or len(title) > 200:
                issues.append(f'Topic {order} needs a non-empty title of at most 200 characters and was omitted.')
                continue

            candidate_issues: list[str] = []
            source_order = topic.get('source_order')
            if isinstance(source_order, bool) or not isinstance(source_order, int) or source_order != order:
                candidate_issues.append('The source order is missing, duplicated, or inconsistent with the source sequence.')
            original_number = topic.get('original_number')
            if original_number is not None and (
                not isinstance(original_number, str)
                or not original_number.strip()
                or len(original_number) > 40
                or re.search(rf'(?<![\w.]){re.escape(original_number.strip())}(?![\w.])', source_text) is None
            ):
                candidate_issues.append('Original numbering is not verifiable in the extracted source text.')
                original_number = None
            if original_number and parent_number and not original_number.startswith(f'{parent_number}.'):
                candidate_issues.append('The numbered topic does not clearly belong beneath its numbered parent.')

            summary = topic.get('summary')
            if not isinstance(summary, str) or not 10 <= len(summary.strip()) <= 5000:
                candidate_issues.append('Summary is missing or outside the 10-to-5000-character range.')
                summary = summary.strip() if isinstance(summary, str) else ''
            objectives = topic.get('learning_objectives')
            if not isinstance(objectives, list) or len(objectives) > 10 or any(not isinstance(item, str) for item in objectives):
                candidate_issues.append('Learning objectives must be an array of at most ten strings.')
                objectives = []
            objectives = [item.strip() for item in objectives if isinstance(item, str) and item.strip()]
            if not objectives:
                candidate_issues.append('At least one source-supported learning objective is required.')
            concepts = topic.get('concepts')
            if not isinstance(concepts, list) or len(concepts) > 30 or any(not isinstance(item, str) for item in concepts):
                candidate_issues.append('Concepts must be an array of at most thirty strings.')
                concepts = []
            concepts = [item.strip() for item in concepts if isinstance(item, str) and item.strip()]
            if not concepts:
                candidate_issues.append('At least one source-supported concept is required.')

            excerpt = topic.get('source_excerpt')
            locator = topic.get('source_locator')
            if locator is not None and (
                not isinstance(locator, dict)
                or set(locator) != {'type', 'value'}
                or not isinstance(locator.get('type'), str)
                or locator.get('type') not in {'page', 'slide'}
                or isinstance(locator.get('value'), bool)
                or not isinstance(locator.get('value'), int)
                or locator.get('value') < 1
            ):
                candidate_issues.append('Source locator must identify a positive page or slide extracted by the document reader.')
                locator = None
            segment = _source_segment(source_text, locator, allowed_locator_types)
            if locator is not None and segment is None:
                locator = None
            if not isinstance(excerpt, str) or len(excerpt.strip()) < 8 or segment is None or _normalized(excerpt) not in _normalized(segment):
                candidate_issues.append(
                    'Source excerpt is not grounded in the claimed source text/location.'
                )
                excerpt = excerpt.strip() if isinstance(excerpt, str) else None
            elif _normalized(title) not in _normalized(excerpt):
                candidate_issues.append('Topic title is not explicitly present in its cited excerpt; verify it against the source.')

            order_uncertain = topic.get('order_uncertain', False)
            hierarchy_uncertain = topic.get('hierarchy_uncertain', False)
            requires_review = topic.get('requires_review', False)
            review_reason = topic.get('review_reason')
            if not isinstance(order_uncertain, bool):
                candidate_issues.append('Order uncertainty must be a boolean.')
                order_uncertain = True
            if not isinstance(hierarchy_uncertain, bool):
                candidate_issues.append('Hierarchy uncertainty must be a boolean.')
                hierarchy_uncertain = True
            if not isinstance(requires_review, bool):
                candidate_issues.append('The review flag must be a boolean.')
                requires_review = True
            if review_reason is not None and (not isinstance(review_reason, str) or len(review_reason) > 1000):
                candidate_issues.append('Review reason must be null or a string of at most 1000 characters.')
                review_reason = None
            if order_uncertain:
                candidate_issues.append('Source ordering is uncertain; confirm the sequence against the source.')
            if hierarchy_uncertain:
                candidate_issues.append('Parent-child hierarchy is uncertain; confirm the relationship against the source.')
            if requires_review and not (isinstance(review_reason, str) and review_reason.strip()):
                candidate_issues.append('The model marked this topic for review but did not explain why.')
            if review_reason and not requires_review:
                candidate_issues.append('A review reason was provided without setting requires_review=true.')
            candidate_issues.extend(
                f'Candidate needs review: {reason}'
                for reason in ([review_reason.strip()] if isinstance(review_reason, str) and review_reason.strip() else [])
                if requires_review
            )

            duplicate_key = (
                _normalized(title),
                _normalized(original_number or ''),
                _normalized(parent_number or ''),
            )
            if duplicate_key in seen_candidates:
                candidate_issues.append('This candidate duplicates another topic in the same source context.')
            seen_candidates.add(duplicate_key)

            candidate_id = '.'.join(str(value) for value in candidate_path)
            child_items = topic.get('children')
            if not isinstance(child_items, list):
                candidate_issues.append('Subtopics must be an array.')
                child_items = []
            children = validate_topic_list(
                child_items,
                original_number,
                candidate_path,
                depth + 1,
            ) if child_items else []
            if candidate_issues:
                requires_review = True
                review_reason = '; '.join(dict.fromkeys(
                    ([review_reason.strip()] if isinstance(review_reason, str) and review_reason.strip() else [])
                    + candidate_issues
                ))[:1000]
                issues.extend(f'Topic {order}: {item}' for item in candidate_issues)

            output.append({
                'candidate_id': candidate_id,
                'candidate_path': list(candidate_path),
                'source_order': order,
                'original_number': original_number.strip() if isinstance(original_number, str) else None,
                'title': title.strip(),
                'summary': summary,
                'learning_objectives': objectives,
                'concepts': concepts,
                'source_excerpt': excerpt,
                'source_locator': locator,
                'order_uncertain': order_uncertain,
                'hierarchy_uncertain': hierarchy_uncertain,
                'requires_review': requires_review,
                'review_reason': review_reason,
                'children': children,
            })
        return output

    validated = validate_topic_list(topics, None, (), 1)
    module = expected_module if isinstance(expected_module, dict) else {}
    def add_syllabus_metadata(items: list[dict[str, Any]]) -> None:
        for topic in items:
            topic['course_title'] = course_title
            topic['module_number'] = module.get('number')
            topic['module_title'] = module.get('title')
            add_syllabus_metadata(topic.get('children', []))

    add_syllabus_metadata(validated)
    if isinstance(limitation, str) and limitation.strip():
        issues.append(f'The topic extractor reported a limitation: {limitation.strip()}')
    elif limitation is not None:
        issues.append('Topic-map limitation must be null or a string.')
    return validated, list(dict.fromkeys(issues)), limitation if isinstance(limitation, str) else None


def validate_assessment_response(raw: str, passages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str], str | None]:
    try:
        result = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return [], ['The model response was not valid JSON.'], None
    if not isinstance(result, dict) or set(result) - {'questions', 'limitation'}:
        return [], ['The model response must contain only questions and an optional limitation.'], None
    questions = result.get('questions')
    limitation = result.get('limitation')
    if not isinstance(questions, list):
        return [], ['The model response did not include a questions array.'], limitation if isinstance(limitation, str) else None
    issues: list[str] = []
    mcqs = [item for item in questions if isinstance(item, dict) and item.get('question_type') == 'MCQ']
    short_answers = [item for item in questions if isinstance(item, dict) and item.get('question_type') == 'SHORT_ANSWER']
    if len(questions) != 20 or len(mcqs) != 15 or len(short_answers) != 5:
        issues.append(f'Expected exactly 15 MCQs and 5 short-answer questions; received {len(mcqs)} and {len(short_answers)}.')
    if any(not isinstance(item, dict) or item.get('question_type') not in {'MCQ', 'SHORT_ANSWER'} for item in questions):
        issues.append('Every question must have a supported question type.')
    passage_by_ref = {str(item.get('ref')): item for item in passages if item.get('ref')}
    seen_questions: list[set[str]] = []
    validated: list[dict[str, Any]] = []

    for index, item in enumerate(questions, start=1):
        if not isinstance(item, dict):
            continue
        question = item.get('question')
        if not isinstance(question, str) or len(question.strip()) < 12:
            issues.append(f'Question {index} is missing a clear question statement.')
            continue
        question_words = _content_words(question)
        if any(
            question_words == prior or (question_words | prior and len(question_words & prior) / len(question_words | prior) >= 0.9)
            for prior in seen_questions
        ):
            issues.append(f'Question {index} duplicates or closely paraphrases another question.')
        seen_questions.append(question_words)
        evidence = item.get('source_evidence')
        if not isinstance(evidence, list) or not evidence:
            issues.append(f'Question {index} has no source evidence.')
            evidence = []
        safe_evidence = []
        for citation in evidence:
            if not isinstance(citation, dict):
                continue
            ref, quote = citation.get('ref'), citation.get('quote')
            passage = passage_by_ref.get(str(ref))
            if not passage or not isinstance(quote, str) or len(quote.strip()) < 8:
                issues.append(f'Question {index} cites an unknown passage or an insufficient quote.')
                continue
            if _normalized(quote) not in _normalized(str(passage.get('text', ''))):
                issues.append(f'Question {index} source quote does not match its retrieved passage.')
                continue
            safe_evidence.append({'ref': str(ref), 'quote': quote.strip()})
        if not safe_evidence:
            continue
        source_words = _content_words(' '.join(item['quote'] for item in safe_evidence))
        learning_objective = item.get('learning_objective')
        correct_answer = item.get('correct_answer')
        if not isinstance(learning_objective, str) or not learning_objective.strip():
            issues.append(f'Question {index} is missing its learning objective.')
        if not isinstance(correct_answer, str) or not correct_answer.strip():
            issues.append(f'Question {index} is missing its answer key or reference answer.')
        question_type = item.get('question_type')
        options = item.get('options')
        rubric = item.get('rubric')
        if question_type == 'MCQ':
            if not isinstance(options, list) or len(options) != 4 or any(not isinstance(option, str) or not option.strip() for option in options):
                issues.append(f'MCQ {index} must have exactly four non-empty options.')
            elif len({_normalized(option) for option in options}) != 4:
                issues.append(f'MCQ {index} contains duplicate answer options.')
            if correct_answer not in {'A', 'B', 'C', 'D'}:
                issues.append(f'MCQ {index} must have exactly one valid answer key from A to D.')
            elif isinstance(options, list) and len(options) == 4:
                answer_words = _content_words(options[ord(correct_answer) - ord('A')])
                if not answer_words.intersection(source_words):
                    issues.append(f'MCQ {index} answer key is not supported by its cited source quote.')
            if not isinstance(item.get('explanation'), str) or not item['explanation'].strip():
                issues.append(f'MCQ {index} is missing its explanation.')
            rubric = {}
        elif question_type == 'SHORT_ANSWER':
            if options not in ([], None):
                issues.append(f'Short-answer question {index} must not include options.')
            if not isinstance(rubric, dict) or not isinstance(rubric.get('criteria'), list) or not rubric['criteria']:
                issues.append(f'Short-answer question {index} needs a rubric with criteria.')
            elif any(
                not isinstance(criterion, dict)
                or not isinstance(criterion.get('criterion'), str)
                or not criterion['criterion'].strip()
                or isinstance(criterion.get('marks'), bool)
                or not isinstance(criterion.get('marks'), (int, float))
                or criterion['marks'] <= 0
                or not isinstance(criterion.get('partial_credit'), str)
                or not criterion['partial_credit'].strip()
                for criterion in rubric['criteria']
            ):
                issues.append(f'Short-answer question {index} rubric criteria need explicit partial-credit rules.')
            elif abs(sum(float(criterion['marks']) for criterion in rubric['criteria']) - 5.0) > 0.001:
                issues.append(f'Short-answer question {index} rubric criteria must total five marks.')
            elif rubric.get('max_marks') != 5:
                issues.append(f'Short-answer question {index} rubric max_marks must be 5.')
            answer_words = _content_words(correct_answer) if isinstance(correct_answer, str) else set()
            if not answer_words.intersection(source_words):
                issues.append(f'Short-answer question {index} reference answer is not supported by its cited source quote.')
        validated.append({
            'question_type': question_type,
            'question': question.strip(),
            'options': options if question_type == 'MCQ' and isinstance(options, list) else [],
            'correct_answer': correct_answer.strip() if isinstance(correct_answer, str) else '',
            'explanation': item.get('explanation', '').strip() if isinstance(item.get('explanation'), str) else '',
            'learning_objective': learning_objective.strip() if isinstance(learning_objective, str) else '',
            'difficulty': item.get('difficulty') if isinstance(item.get('difficulty'), str) else 'UNSPECIFIED',
            'rubric': rubric if isinstance(rubric, dict) else {},
            'source_evidence': safe_evidence,
        })
    if isinstance(limitation, str) and limitation.strip():
        issues.append(f'The model reported insufficient evidence: {limitation.strip()}')
    return validated, list(dict.fromkeys(issues)), limitation if isinstance(limitation, str) else None


def validate_short_answer_evaluation(raw: str) -> dict[str, Any]:
    try:
        result = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError('The evaluator response was not valid JSON.') from exc
    expected_keys = {'marks_awarded', 'feedback', 'confidence', 'needs_review', 'criteria'}
    if not isinstance(result, dict) or set(result) != expected_keys:
        raise ValueError('The evaluator response did not match the required grading schema.')
    marks = result['marks_awarded']
    confidence = result['confidence']
    if isinstance(marks, bool) or not isinstance(marks, (int, float)) or not 0 <= marks <= 1:
        raise ValueError('The evaluator marks must be between 0 and 1.')
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ValueError('The evaluator confidence must be between 0 and 1.')
    if not isinstance(result['feedback'], str) or not result['feedback'].strip():
        raise ValueError('The evaluator must provide feedback.')
    if not isinstance(result['needs_review'], bool) or not isinstance(result['criteria'], list):
        raise ValueError('The evaluator review flag or criteria are invalid.')
    result['marks_awarded'] = float(marks)
    result['confidence'] = float(confidence)
    result['needs_review'] = bool(result['needs_review'] or confidence < 0.8)
    return result