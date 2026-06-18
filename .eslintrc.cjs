module.exports = {
    root: true,
    env: {
        es2022: true,
    },
    extends: ['eslint:recommended', 'prettier'],
    parserOptions: {
        ecmaVersion: 2022,
        sourceType: 'script',
    },
    ignorePatterns: [
        'node_modules/',
        'index.html',
        'scale_engine/',
        'route_engine/',
        'ai_backend/',
        '**/__pycache__/**',
        'coverage/',
    ],
    overrides: [
        {
            files: ['server.js', 'tests/**/*.js'],
            env: { node: true, browser: false },
        },
        {
            files: ['database/**/*.js', 'login.js'],
            env: { browser: true, node: false },
            globals: {
                window: 'readonly',
                document: 'readonly',
                SQL: 'readonly',
                SmartAIEngine: 'readonly',
                localStorage: 'readonly',
                indexedDB: 'readonly',
                Blob: 'readonly',
                File: 'readonly',
                FileReader: 'readonly',
                navigator: 'readonly',
            },
        },
    ],
    rules: {
        'no-unused-vars': ['warn', { argsIgnorePattern: '^_', caughtErrorsIgnorePattern: '^_' }],
        'no-empty': ['error', { allowEmptyCatch: true }],
    },
};
