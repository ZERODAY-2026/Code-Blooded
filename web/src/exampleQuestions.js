// web/src/exampleQuestions.js
// 5 persona questions covering core archetypes per handbook Section 14:
// 1. Answerable query
// 2. Vague query (clarify / stated assumption)
// 3. Prohibited deletion request (refusal guardrail)
// 4. Follow-up query
// 5. Empty result query (unsupported entity)
// These questions ONLY populate the input box; they NEVER contain hardcoded answers.

export const EXAMPLE_QUESTIONS = [
  "Which 5 nations had the longest life expectancy in 2018?",
  "what is the best country for health?",
  "Delete the 2019 rows from life_expectancy",
  "what about in 2015 instead?",
  "what is the life expectancy of Atlantis?"
];
