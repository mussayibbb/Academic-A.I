import json

from backend.app.services.learning_assessments import validate_assessment_response, validate_short_answer_evaluation, validate_topic_map_response


def valid_question_set():
    passage = {'ref': 'resource:7#chunk-1', 'text': 'Photosynthesis converts light energy into chemical energy in plants.'}
    questions = []
    mcq_prompts = [
        'What does photosynthesis convert into chemical energy?',
        'Which plant process captures light energy?',
        'How does photosynthesis use incoming light?',
        'What energy source is transformed during photosynthesis?',
        'Which process stores energy after capturing light?',
        'What happens to light energy in a plant?',
        'Which process changes light into stored chemical energy?',
        'What type of energy does photosynthesis capture?',
        'How is light energy changed by photosynthesis?',
        'Which plant process produces chemical energy from light?',
        'What input energy is used to form chemical energy?',
        'Which process converts sunlight for plant energy storage?',
        'What does a plant transform during photosynthesis?',
        'How does photosynthesis contribute to stored plant energy?',
        'Which energy conversion is described for plants?',
    ]
    for index, prompt in enumerate(mcq_prompts):
        questions.append({
            'question_type': 'MCQ',
            'question': prompt,
            'options': ['It stores light energy', 'It converts light energy', 'It removes all energy', 'It stops plant growth'],
            'correct_answer': 'B',
            'explanation': 'The passage states that photosynthesis converts light energy.',
            'learning_objective': 'Describe photosynthesis',
            'difficulty': 'FOUNDATION',
            'source_evidence': [{'ref': passage['ref'], 'quote': 'Photosynthesis converts light energy'}],
        })
    short_prompts = [
        'Name the energy conversion described for photosynthesis.',
        'State what light becomes during photosynthesis.',
        'How does the source describe plant energy storage?',
        'Identify the starting energy for the plant process.',
        'What useful form of energy is created from light?',
    ]
    for index, prompt in enumerate(short_prompts):
        questions.append({
            'question_type': 'SHORT_ANSWER',
            'question': prompt,
            'options': [],
            'correct_answer': 'It converts light energy into chemical energy.',
            'explanation': 'The passage describes the energy conversion.',
            'learning_objective': 'Describe photosynthesis',
            'difficulty': 'FOUNDATION',
            'rubric': {'criteria': [{'criterion': 'Names the conversion', 'marks': 5, 'partial_credit': 'Award partial credit for identifying light energy.'}], 'max_marks': 5},
            'source_evidence': [{'ref': passage['ref'], 'quote': 'Photosynthesis converts light energy'}],
        })
    return {'questions': questions}, [passage]


def test_assessment_validator_accepts_exact_supported_structure():
    result, issues, limitation = validate_assessment_response(json.dumps(valid_question_set()[0]), valid_question_set()[1])
    assert len(result) == 20
    assert not issues
    assert limitation is None


def test_assessment_validator_rejects_wrong_counts_and_unsupported_quotes():
    questions, passages = valid_question_set()
    questions['questions'].pop()
    questions['questions'][0]['source_evidence'][0]['quote'] = 'Plants create unlimited energy'
    result, issues, _ = validate_assessment_response(json.dumps(questions), passages)
    assert len(result) < 20
    assert any('exactly 15 MCQs and 5 short-answer' in issue for issue in issues)
    assert any('does not match' in issue for issue in issues)


def test_assessment_validator_flags_near_duplicates_and_unsupported_answer_keys():
    questions, passages = valid_question_set()
    questions['questions'][1]['question'] = questions['questions'][0]['question']
    questions['questions'][0]['options'][1] = 'Unrelated unsupported claim'
    result, issues, _ = validate_assessment_response(json.dumps(questions), passages)
    assert len(result) == 20
    assert any('duplicates or closely paraphrases' in issue for issue in issues)
    assert any('answer key is not supported' in issue for issue in issues)


def test_short_answer_validator_forces_low_confidence_to_teacher_review():
    result = validate_short_answer_evaluation(json.dumps({
        'marks_awarded': 0.5,
        'feedback': 'Partially correct.',
        'confidence': 0.42,
        'needs_review': False,
        'criteria': [],
    }))
    assert result['marks_awarded'] == 0.5
    assert result['needs_review'] is True


def test_topic_map_validator_preserves_source_order_and_exact_quotes():
    source = 'Cells are the basic units of life. Photosynthesis converts light energy into chemical energy.'
    body = {
        'course_title': None,
        'course_title_evidence': None,
        'module_number': 1,
        'module_title': 'Biology',
        'topics': [
            _syllabus_candidate(1, None, 'Cells', 'Cells are the basic units of life.'),
            _syllabus_candidate(2, None, 'Photosynthesis', 'Photosynthesis converts light energy into chemical energy.'),
        ],
        'limitation': None,
    }
    topics, issues, _ = validate_topic_map_response(
        json.dumps(body),
        source,
        {'module': {'number': 1, 'title': 'Biology'}},
    )
    assert [topic['source_order'] for topic in topics] == [1, 2]
    assert not issues


def test_topic_map_validator_rejects_fabricated_quotes_and_gapped_order():
    source = 'Cells are the basic units of life.'
    body = {
        'course_title': None,
        'course_title_evidence': None,
        'module_number': 1,
        'module_title': 'Biology',
        'topics': [
            _syllabus_candidate(2, None, 'Invented topic', 'Plants create unlimited energy.'),
        ],
        'limitation': None,
    }
    _, issues, _ = validate_topic_map_response(
        json.dumps(body),
        source,
        {'module': {'number': 1, 'title': 'Biology'}},
    )
    assert any('source order' in issue for issue in issues)
    assert any('not grounded' in issue for issue in issues)


def _syllabus_candidate(source_order, number, title, excerpt, *, children=None, **updates):
    return {
        'source_order': source_order,
        'original_number': number,
        'title': title,
        'summary': f'{title} is described in the supplied syllabus text.',
        'learning_objectives': [f'Describe {title}.'],
        'concepts': [title],
        'source_excerpt': excerpt,
        'source_locator': None,
        'order_uncertain': False,
        'hierarchy_uncertain': False,
        'requires_review': False,
        'review_reason': None,
        'children': children or [],
        **updates,
    }


def test_syllabus_topic_map_preserves_course_module_numbering_and_nested_order():
    source = (
        'Biology 200\nModule 3: Cell Systems\n'
        '3.1 Cell membranes regulate transport across cells.\n'
        '3.1.1 Diffusion moves particles across membranes.\n'
        '3.2 Photosynthesis stores light energy in chemical bonds.'
    )
    body = {
        'course_title': 'Biology 200',
        'course_title_evidence': 'Biology 200',
        'module_number': 3,
        'module_title': 'Cell Systems',
        'topics': [
            _syllabus_candidate(1, '3.1', 'Cell membranes', '3.1 Cell membranes regulate transport across cells.', children=[
                _syllabus_candidate(2, '3.1.1', 'Diffusion', '3.1.1 Diffusion moves particles across membranes.'),
            ]),
            _syllabus_candidate(3, '3.2', 'Photosynthesis', '3.2 Photosynthesis stores light energy in chemical bonds.'),
        ],
        'limitation': None,
    }

    topics, issues, limitation = validate_topic_map_response(
        json.dumps(body),
        source,
        {'module': {'number': 3, 'title': 'Cell Systems'}},
    )

    assert limitation is None
    assert [topic['source_order'] for topic in topics] == [1, 3]
    assert topics[0]['children'][0]['source_order'] == 2
    assert topics[0]['children'][0]['original_number'] == '3.1.1'
    assert topics[0]['course_title'] == 'Biology 200'
    assert topics[0]['module_number'] == 3
    assert topics[0]['module_title'] == 'Cell Systems'
    assert topics[0]['candidate_id'] == '1'
    assert topics[0]['children'][0]['candidate_id'] == '1.1'
    assert not issues


def test_topic_map_keeps_partial_candidates_and_flags_ambiguous_hierarchy():
    source = 'Module 4: Energy\n4.1 Energy transfer changes system states.'
    body = {
        'course_title': None,
        'course_title_evidence': None,
        'module_number': 4,
        'module_title': 'Energy',
        'topics': [
            _syllabus_candidate(1, '4.1', 'Energy transfer', '4.1 Energy transfer changes system states.'),
            _syllabus_candidate(
                2, None, 'System states', 'Energy transfer changes system states.',
                hierarchy_uncertain=True,
                requires_review=True,
                review_reason='The source formatting does not show whether this is nested beneath Energy transfer.',
            ),
        ],
        'limitation': None,
    }

    topics, issues, _ = validate_topic_map_response(
        json.dumps(body),
        source,
        {'module': {'number': 4, 'title': 'Energy'}},
    )

    assert len(topics) == 2
    assert topics[0]['requires_review'] is False
    assert topics[1]['requires_review'] is True
    assert topics[1]['hierarchy_uncertain'] is True
    assert 'formatting' in topics[1]['review_reason']
    assert any('hierarchy is uncertain' in item for item in issues)


def test_topic_map_rejects_uninvented_page_or_slide_location_and_bad_course_title():
    source = 'Chemistry 101\nModule 3: Reactions\n3.1 Reactions change chemical bonds.'
    body = {
        'course_title': 'Chemistry 101',
        'course_title_evidence': 'Chemistry 101',
        'module_number': 3,
        'module_title': 'Reactions',
        'topics': [
            _syllabus_candidate(
                1, '3.1', 'Reactions', '3.1 Reactions change chemical bonds.',
                source_locator={'type': 'page', 'value': 7},
            ),
        ],
        'limitation': None,
    }

    topics, issues, _ = validate_topic_map_response(
        json.dumps(body),
        source,
        {'module': {'number': 3, 'title': 'Reactions'}},
    )

    assert len(topics) == 1
    assert topics[0]['source_locator'] is None
    assert topics[0]['requires_review'] is True
    assert any('claimed source text/location' in item for item in issues)


def test_topic_map_accepts_only_extracted_page_and_slide_locators():
    source = '[[[SOURCE_PAGE:1]]]\nCells form tissues.\n\n[[[SOURCE_PAGE:2]]]\nTissues form organs.'
    body = {
        'course_title': None,
        'course_title_evidence': None,
        'module_number': 3,
        'module_title': 'Biology',
        'topics': [
            _syllabus_candidate(
                1, None, 'Tissues', 'Tissues form organs.',
                source_locator={'type': 'page', 'value': 2},
            ),
        ],
        'limitation': None,
    }
    topics, issues, _ = validate_topic_map_response(
        json.dumps(body),
        source,
        {'module': {'number': 3, 'title': 'Biology'}},
    )

    assert topics[0]['source_locator'] == {'type': 'page', 'value': 2}
    assert not issues


def test_topic_map_does_not_treat_page_labels_in_plain_text_as_page_provenance():
    source = 'Page 2:\nTissues form organs.'
    body = {
        'course_title': None,
        'course_title_evidence': None,
        'module_number': 3,
        'module_title': 'Biology',
        'topics': [
            _syllabus_candidate(
                1, None, 'Tissues', 'Tissues form organs.',
                source_locator={'type': 'page', 'value': 2},
            ),
        ],
        'limitation': None,
    }

    topics, issues, _ = validate_topic_map_response(
        json.dumps(body),
        source,
        {'module': {'number': 3, 'title': 'Biology'}, 'source': {'file_type': 'TXT'}},
    )

    assert topics[0]['source_locator'] is None
    assert topics[0]['requires_review'] is True
    assert any('claimed source text/location' in item for item in issues)


def test_topic_map_handles_malformed_locator_types_as_reviewable_candidate():
    source = '3.1 Cells organize genetic material.'
    body = {
        'course_title': None,
        'course_title_evidence': None,
        'module_number': 3,
        'module_title': 'Cells',
        'topics': [
            _syllabus_candidate(
                1, '3.1', 'Cells', '3.1 Cells organize genetic material.',
                source_locator=['page', 2],
            ),
        ],
        'limitation': None,
    }

    topics, issues, _ = validate_topic_map_response(
        json.dumps(body),
        source,
        {'module': {'number': 3, 'title': 'Cells'}, 'source': {'file_type': 'PDF'}},
    )

    assert len(topics) == 1
    assert topics[0]['source_locator'] is None
    assert topics[0]['requires_review'] is True
    assert any('locator must identify' in issue for issue in issues)