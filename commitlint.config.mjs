// Conventional Commits — https://www.conventionalcommits.org/
export default {
  extends: ['@commitlint/config-conventional'],
  // Merge commits (e.g. "Merge origin/main after PR #25") carry no type; the default
  // ignore pattern only matches a few phrasings, so ignore every "Merge …" subject.
  ignores: [(message) => /^Merge\b/.test(message)],
  rules: {
    'type-enum': [
      2,
      'always',
      ['build', 'chore', 'ci', 'docs', 'feat', 'fix', 'perf', 'refactor', 'revert', 'style', 'test', 'deps'],
    ],
    'scope-enum': [1, 'always', ['web', 'data', 'scripts', 'osm', 'ci', 'deps', 'docs']],
    'body-max-line-length': [0],
    'footer-max-line-length': [0],
    // Allow sentence-case (capitalized first word) and lowercase.
    // Still reject Start Case, PascalCase, and UPPER CASE.
    // @commitlint/config-conventional forbids sentence-case too; that rejects
    // subjects such as "Bump the actions group with 4 updates".
    'subject-case': [2, 'never', ['start-case', 'pascal-case', 'upper-case']],
  },
};
