# Third-party licenses / licenças de terceiros

MIT covers original Tabular Drift Audit source only. It does not relicense Python, the base image, scientific wheels, optional pandas, build tools or bundled native libraries. Exact immutable wheel/source URLs, byte sizes and SHA256 are in `locks/artifacts.json`; actual install hashes are in the resolved lock files.

The preparation script extracts each wheel's exact license/copyright/NOTICE members into `notices/<wheel-name>/...`, including bundled native notices. It retains exact source archives without executing them and copies actual base-image Python/Debian notices. The runtime image includes those notices at `/opt/notices`. A source-only application wheel does not bundle the scientific implementation; separately installed dependencies keep their own licenses.

| Component | Exact version | Role |
| --- | --- | --- |
| build | 1.6.1 | Build/test preparation; exact upstream notices collected |
| numpy | 2.5.4 | Core runtime; exact upstream notices collected |
| packaging | 26.3 | Build/test preparation; exact upstream notices collected |
| pandas | 3.1.1 | Optional pandas closure; exact upstream notices collected |
| pyarrow | 26.0.1 | Core runtime; exact upstream notices collected |
| pyproject_hooks | 1.3.3 | Build/test preparation; exact upstream notices collected |
| python-dateutil | 4.0.0 | Optional pandas closure; exact upstream notices collected |
| scipy | 1.18.1 | Core runtime; exact upstream notices collected |
| setuptools | 80.9.0 | Build/test preparation; exact upstream notices collected |
| tzdata | 2026.6 | Optional pandas closure; exact upstream notices collected |
| wheel | 0.45.1 | Build/test preparation; exact upstream notices collected |

A versão fixada do backend é setuptools80.9.0 com wheel0.45.1. São artefatos realmente selecionados, não uma alegação de versões mais recentes. A preparação mantém licenças/NOTICE exatos das bibliotecas nativas incluídas nas distribuições, além de Python e base Debian. O runtime preserva os avisos em `/opt/notices`; a licença MIT da aplicação não substitui nenhuma licença das dependências.
