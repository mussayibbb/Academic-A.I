// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import StudentRecoveryQuizzes from './StudentRecoveryQuizzes';

const mocks = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('@/lib/api', () => ({ api: mocks.api }));

const quiz = {
  task_id: 31,
  plan_id: 7,
  subject: 'Biology',
  topic: 'Cell structure',
  day_no: 1,
  scheduled_date: '2026-11-18',
  title: 'Biology · Cell structure',
  max_score: 10,
  questions: [
    { question: 'Choose a cell type.', question_type: 'MCQ', options: ['Plant', 'Rock', 'Cloud', 'Water'], marks: 2 },
    { question: 'Name this cell part.', question_type: 'SHORT_ANSWER', options: [], marks: 2 },
    { question: 'Choose a molecule.', question_type: 'MCQ', options: ['DNA', 'Stone', 'Air', 'Salt'], marks: 2 },
    { question: 'Name this process.', question_type: 'SHORT_ANSWER', options: [], marks: 2 },
    { question: 'Choose a structure.', question_type: 'MCQ', options: ['Nucleus', 'River', 'Mountain', 'Ocean'], marks: 2 },
  ],
};

describe('student daily recovery quizzes', () => {
  beforeEach(() => {
    mocks.api.mockImplementation(async (path: string) => {
      if (path === '/api/v1/students/me/recovery-quizzes/today') return { date: '2026-11-18', items: [quiz] };
      if (path === '/api/v1/students/me/recovery-quizzes/31/attempts') {
        return {
          score: 8,
          max_score: 10,
          percent: 80,
          results: [{
            question: quiz.questions[0].question,
            answer: 'A',
            correct: true,
            correct_answer: 'A',
            explanation: 'Plant cells are discussed in the module.',
            score: 2,
            max_score: 2,
          }],
        };
      }
      throw new Error(`Unexpected API call: ${path}`);
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('loads due quizzes, submits answers, and displays the saved auto-grade', async () => {
    render(<StudentRecoveryQuizzes />);
    expect(await screen.findByRole('heading', { name: 'Today’s recovery quizzes' })).toBeTruthy();
    const form = await screen.findByRole('heading', { name: 'Biology · Cell structure' }).then((heading) => heading.closest('form'));
    if (!form) throw new Error('Quiz form was not rendered.');

    fireEvent.click(within(form).getByLabelText('A. Plant'));
    fireEvent.change(within(form).getAllByLabelText('Your answer')[0], { target: { value: 'Nucleus' } });
    fireEvent.click(within(form).getByLabelText('A. DNA'));
    fireEvent.change(within(form).getAllByLabelText('Your answer')[1], { target: { value: 'Mitosis' } });
    fireEvent.click(within(form).getByLabelText('A. Nucleus'));
    fireEvent.submit(form);

    expect((await screen.findByRole('status')).textContent).toContain('Quiz graded: 8/10 (80%).');
    expect(mocks.api).toHaveBeenCalledWith(
      '/api/v1/students/me/recovery-quizzes/31/attempts',
      { method: 'POST', body: JSON.stringify({ answers: { '0': 'A', '1': 'Nucleus', '2': 'A', '3': 'Mitosis', '4': 'A' } }) },
    );
    expect(await screen.findByText('Submitted quiz · 8/10 (80%)')).toBeTruthy();
  });
});
