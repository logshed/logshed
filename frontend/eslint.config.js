// Lint rules for the frontend (npm run lint). tsc --strict already checks types and unused code;
// ESLint adds the React hooks rules and the typescript-eslint recommended set, with no formatting rules.
import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import reactHooks from 'eslint-plugin-react-hooks';

export default tseslint.config(
  { ignores: ['dist-demo/**', 'node_modules/**'] },
  js.configs.recommended,
  tseslint.configs.recommended,
  {
    files: ['**/*.{ts,tsx}'],
    plugins: { 'react-hooks': reactHooks },
    rules: {
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'error',
      // tsc --strict is the type check; the code uses any on purpose where a library's types do not help
      '@typescript-eslint/no-explicit-any': 'off',
      // A leading underscore marks a value left unused on purpose, such as a field split off with ...rest
      '@typescript-eslint/no-unused-vars': ['error', {
        argsIgnorePattern: '^_',
        varsIgnorePattern: '^_',
        destructuredArrayIgnorePattern: '^_',
        caughtErrorsIgnorePattern: '^_',
      }],
    },
  },
);
