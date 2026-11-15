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
  {
    // Screens build their controls from components/common/ (docs/spec/frontend.md, section 4), which alone
    // may use these elements. The browser's own <select> and suggestion popup look different on every
    // system and ignore the dark theme, and the dropdowns add search, custom values and keyboard handling.
    files: ['src/components/**/*.tsx', 'src/App.tsx'],
    ignores: ['src/components/common/**'],
    rules: {
      'no-restricted-syntax': ['error',
        {
          selector: "JSXOpeningElement[name.name='select']",
          message: 'Use SelectDropdown (components/common/SelectDropdown.tsx; variant "panel" in forms and dialogs, "toolbar" in toolbars), not a native <select>.',
        },
        {
          selector: "JSXOpeningElement[name.name='datalist']",
          message: 'Use MultiSelectDropdown (components/common/MultiSelectDropdown.tsx; variant "form", with singleSelect for one value and allowCustomInput for typed names), not a <datalist>.',
        },
        {
          selector: "JSXOpeningElement[name.name=/^(input|TextField)$/] > JSXAttribute[name.name='list']",
          message: 'Use MultiSelectDropdown (components/common/MultiSelectDropdown.tsx; variant "form", with singleSelect for one value and allowCustomInput for typed names), not a text box with list=.',
        },
      ],
    },
  },
);
