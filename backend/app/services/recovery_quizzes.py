import json
import re
from datetime import date, timedelta
from math import isfinite
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GeneratedQuestion(BaseModel):
    model_config = ConfigDict(extra='forbid')

    question: str = Field(min_length=8, max_length=1000)
    module_no: int = Field(ge=1, le=5)
    question_type: Literal['MCQ', 'SHORT_ANSWER']
    options: list[str] = Field(max_length=4)
    correct_answer: str = Field(min_length=1, max_length=1000)
    explanation: str = Field(min_length=1, max_length=2000)
    marks: float = Field(gt=0, le=10, allow_inf_nan=False)
    source_excerpt: str = Field(min_length=8, max_length=1000)

    @model_validator(mode='after')
    def validate_answer_shape(self):
        if self.question_type == 'MCQ':
            if len(self.options) != 4 or any(not item.strip() for item in self.options):
                raise ValueError('MCQs must have four non-empty options.')
            if self.correct_answer not in {'A', 'B', 'C', 'D'}:
                raise ValueError('MCQs must identify the correct option by its A-D key.')
        elif self.options:
            raise ValueError('Short-answer questions must not include options.')
        return self


class GeneratedTopicQuiz(BaseModel):
    model_config = ConfigDict(extra='forbid')

    topic_id: int = Field(gt=0)
    questions: list[GeneratedQuestion] = Field(min_length=5, max_length=5)


class GeneratedQuizSet(BaseModel):
    model_config = ConfigDict(extra='forbid')

    topic_quizzes: list[GeneratedTopicQuiz] = Field(min_length=1, max_length=100)
    one_line_questions: list[GeneratedQuestion] = Field(min_length=5, max_length=5)


def is_backlog(
    score: float,
    observed_maximum: float | None,
    *,
    pass_mark: float,
    maximum_marks: float,
) -> bool:
    if not isfinite(score) or score < 0:
        raise ValueError('FA-1 score must be a finite non-negative number.')
    denominator = maximum_marks if observed_maximum is None else observed_maximum
    if not isfinite(denominator) or denominator <= 0 or score > denominator:
        raise ValueError('FA-1 maximum must be positive and cannot be lower than the score.')
    if not isfinite(pass_mark) or pass_mark <= 0 or pass_mark > maximum_marks:
        raise ValueError('The configured pass mark must be positive and no greater than the maximum.')
    return score * maximum_marks / denominator < pass_mark


def aggregate_recovery_score(attempts: list[tuple[float, float]]) -> float | None:
    if not attempts:
        return None
    score_total = 0.0
    maximum_total = 0.0
    for score, maximum in attempts:
        if not isfinite(score) or not isfinite(maximum) or maximum <= 0 or score < 0 or score > maximum:
            raise ValueError('Attempt scores must be finite and within their positive maximum score.')
        score_total += score
        maximum_total += maximum
    return round(score_total / maximum_total * 100, 1)


def schedule_topics(
    topics: list[Any],
    *,
    topics_per_day: int,
    start_date: date,
) -> list[dict[str, Any]]:
    if topics_per_day not in {1, 2, 3, 5}:
        raise ValueError('topics_per_day must be 1, 2, 3 or 5.')
    scheduled = []
    for index, topic in enumerate(topics):
        day_no = index // topics_per_day + 1
        scheduled.append({
            'topic': topic,
            'day_no': day_no,
            'scheduled_date': start_date + timedelta(days=day_no - 1),
        })
    return scheduled


def _normalized_source(value: str) -> str:
    return re.sub(r'\s+', ' ', value).strip().casefold()


def validate_recovery_quiz_response(
    raw: str,
    *,
    topic_ids: set[int],
    source_text_by_module: dict[int, str],
    topic_modules: dict[int, int],
) -> dict[str, Any]:
    try:
        result = GeneratedQuizSet.model_validate(json.loads(raw))
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError('The AI response did not match the recovery quiz schema.') from exc

    generated_ids = [quiz.topic_id for quiz in result.topic_quizzes]
    if len(generated_ids) != len(set(generated_ids)) or set(generated_ids) != topic_ids:
        raise ValueError('The AI response must include exactly one quiz for each selected topic.')

    def validate_questions(
        questions: list[GeneratedQuestion],
        module_no: int,
        *,
        require_five: bool = True,
    ) -> list[dict[str, Any]]:
        source_text = _normalized_source(source_text_by_module[module_no])
        validated = []
        for question in questions:
            if _normalized_source(question.source_excerpt) not in source_text:
                raise ValueError('A quiz question cited text that does not occur in its selected module.')
            if question.question_type == 'MCQ':
                correct_option = question.options[ord(question.correct_answer) - ord('A')]
                if _normalized_source(correct_option) not in source_text:
                    raise ValueError('An MCQ answer is not supported by its selected module text.')
            elif _normalized_source(question.correct_answer) not in source_text:
                raise ValueError('A short-answer key is not supported by its selected module text.')
            if question.module_no != module_no:
                raise ValueError('A topic quiz question cited a different module than its topic.')
            validated.append(question.model_dump())
        if require_five and (
            len(questions) != 5 or abs(sum(question.marks for question in questions) - 10) > 0.001
        ):
            raise ValueError('Every recovery quiz must contain five questions worth ten marks.')
        return validated

    topic_quizzes = []
    for quiz in result.topic_quizzes:
        module_no = topic_modules.get(quiz.topic_id)
        if module_no is None or module_no not in source_text_by_module:
            raise ValueError('The AI response referenced a topic outside the selected modules.')
        topic_quizzes.append({
            'topic_id': quiz.topic_id,
            'questions': validate_questions(quiz.questions, module_no),
        })
    if any(question.question_type != 'SHORT_ANSWER' for question in result.one_line_questions):
        raise ValueError('The plan-level questions must be short-answer questions.')
    if any(question.marks != 2 for question in result.one_line_questions):
        raise ValueError('The five plan-level one-line questions must each be worth two marks.')
    one_line_questions = []
    for question in result.one_line_questions:
        if question.module_no not in source_text_by_module:
            raise ValueError('A plan-level question referenced an unselected module.')
        one_line_questions.extend(validate_questions(
            [question],
            question.module_no,
            require_five=False,
        ))
    return {'topic_quizzes': topic_quizzes, 'one_line_questions': one_line_questions}


def grade_quiz(questions: list[dict[str, Any]], answers: dict[str, str]) -> tuple[float, list[dict[str, Any]]]:
    if set(answers) != {str(index) for index in range(len(questions))}:
        raise ValueError('Submit one answer for every quiz question.')
    score = 0.0
    results = []
    for index, question in enumerate(questions):
        answer = answers[str(index)].strip()
        if not answer or len(answer) > 2000:
            raise ValueError('Quiz answers must contain 1 to 2000 characters.')
        if question['question_type'] == 'MCQ':
            correct = answer.upper() == question['correct_answer']
        else:
            correct = re.sub(r'\W+', ' ', answer.casefold()).strip() == re.sub(
                r'\W+',
                ' ',
                question['correct_answer'].casefold(),
            ).strip()
        awarded = float(question['marks']) if correct else 0.0
        score += awarded
        results.append({
            'question': question['question'],
            'answer': answer,
            'correct': correct,
            'correct_answer': question['correct_answer'],
            'explanation': question['explanation'],
            'score': awarded,
            'max_score': float(question['marks']),
        })
    return score, results
