import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import skipFormatting from '@vue/eslint-config-prettier/skip-formatting'
import vueTsEslintConfig from '@vue/eslint-config-typescript'

export default [
  {
    name: 'page/files-to-lint',
    files: ['**/*.{js,mjs,vue,ts}'],
  },

  {
    name: 'page/files-to-ignore',
    ignores: ['**/dist/**', '**/coverage/**', '**/node_modules/**'],
  },

  js.configs.recommended,
  ...pluginVue.configs['flat/recommended'],
  ...vueTsEslintConfig(),
  skipFormatting,

  {
    name: 'page/custom-rules',
    rules: {
      'vue/multi-word-component-names': 'off',
      'vue/block-order': ['error', { order: ['template', 'script', 'style'] }],
      'vue/html-self-closing': ['error', { html: { void: 'always', normal: 'never', component: 'always' } }],
      'vue/no-restricted-html-elements': [
        'error',
        {
          element: 'select',
          message: 'Draw the choices as buttons. A native select opens a popup that takes focus, which closes a --close-on-blur window.',
        },
      ],
    },
  },
]
