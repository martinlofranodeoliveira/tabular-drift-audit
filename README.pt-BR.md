# Tabular Drift Audit

Versão de desenvolvimento **0.1.0**. Compare localmente tabelas pequenas de referência e atuais usando distância KS empírica exata, PSI com intervalos fixos, divergência Jensen–Shannon categórica, taxas de ausência e mudanças de esquema. SDK e CLI usam o mesmo núcleo estatístico. Não há modelo, serviço, telemetria ou acesso à rede durante a análise.

## Instalação e uso

O lock qualificado usa Linux x86-64 e Python 3.13, com NumPy 2.5.4, SciPy 1.18.1 e PyArrow 26.0.1.

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/tabular-drift-audit demo --assert
.venv/bin/tabular-drift-audit compare --reference examples/reference.csv --reference-format csv --current examples/current.csv --current-format csv --plan examples/plan.json
```

O demo cria CSV/Parquet sintéticos originais em diretório temporário privado, confere distâncias/contagens e remove os arquivos temporários. JSON sai em stdout; erros com status e código estáveis saem em stderr. Saída 0 indica relatório descritivo completo, inclusive eventuais mudanças de esquema. Não significa uma classificação saudável/com desvio. Erros de entrada, recurso não suportado ou I/O saem com 2; orçamento esgotado com 3; indeterminação numérica com 4.

```python
import pyarrow as pa
from tabular_drift_audit import ColumnPlan, ComparisonPlan, compare_tables

plano = ComparisonPlan([ColumnPlan("score", "numeric", [1, 2])])
relatorio = compare_tables(pa.table({"score": [0., 1., 2.]}),
                           pa.table({"score": [2., 3., 4.]}), plano)
print(relatorio.column("score").numeric.ks_distance.to_fraction())
print(relatorio.to_json())
```

Planos, orçamento e relatórios são imutáveis. As tabelas pertencem a um chamador cooperativo, sem alteração concorrente de buffers. O SDK recebe tabelas Arrow já criadas, não caminhos. Para DataFrames confiáveis, instale `.[pandas]` e invoque explicitamente `compare_dataframes`; o import de pandas é tardio. Tipos object/mistos, booleanos, temporais e extensões arbitrárias são rejeitados.

## Significado das medidas

- **KS:** fração reduzida exata da maior diferença entre CDFs empíricas, com empates completos. Sem valor-p ou alegação de significância.
- **PSI:** intervalos fornecidos pelo chamador, fechados à direita, com classes de extremos, pseudocontagem de 1/2 e logaritmo natural. Essa suavização depende do tamanho amostral. Não há ajuste automático de intervalos ou limiar convencional.
- **JSD categórica:** divergência em bits entre 0 e 1, sem suavização. Não é a distância raiz quadrada. Null é ausência; string vazia e o texto `NaN` são categorias. A identidade UTF-8 é exata e sem normalização.
- **Ausências/esquema:** taxas em frações exatas, diferença assinada atual menos referência e campos adicionados/removidos ou com tipos alterados. Campo ausente ou incompatível com o plano torna a distribuição indisponível; taxas dos campos presentes continuam independentes.

Distribuições sem observações presentes ficam `INSUFFICIENT_NONMISSING`; taxas com zero linhas ficam indisponíveis. Não há zero artificial como sentinela. Relatórios incluem nomes de campos e cortes explícitos, mas não observações, categorias, caminhos ou horários.

## Arquivos e limites

Os leitores da CLI exigem interpretador novo, controlado e próprio, sem pandas, plugins, scripts do usuário ou registro arbitrário de extensões. São aceitos arquivos locais regulares em sistema cooperativo. Symlink na folha, FIFO, diretório, URI remota e referência Parquet a colunas externas são rejeitados. Não existe leitor público de arquivos para SDK em interpretador compartilhado. O [contrato](docs/contract.pt-BR.md) explica as restrições de extensões e memória.

O entrypoint normal confia na inicialização Python, inclusive `PYTHONPATH`, user-site e `sitecustomize`; esses elementos precisam pertencer ao ambiente controlado. `python -I -m tabular_drift_audit ...` exclui entradas adicionais, mas não isola pacotes ou registries arbitrários. A evidência de callback zero vale para o interpretador e pacotes qualificados, não como barreira universal antes do parser. Tabelas Arrow/DataFrames do SDK são objetos Python confiáveis já criados; os dados continuam sujeitos à validação.

CSV usa tipos explícitos definidos pelo plano; campos extras são strings. Campo vazio não citado vira null; vazio entre aspas é string; linhas vazias são preservadas. Campos/cabeçalhos citados com múltiplas linhas e CSV comprimido não fazem parte do subconjunto. Parquet admite campos planos int32/int64/float64, UTF-8/large UTF-8 e dicionários UTF-8 com índices inteiros, com UNCOMPRESSED, SNAPPY ou ZSTD. Tipos aninhados, criptografados, de extensão e outros tipos/codecs não são suportados.

Limites principais: 8 MiB por arquivo, 16.384 linhas por lado, 32 campos de origem/16 selecionados, 128 categorias observadas por campo selecionado, 256 bytes UTF-8 por string, 15 cortes, 64 MiB de dados Arrow selecionados admitidos e 8 MiB de JSON. Alocações nativas podem preceder verificações. Contadores lógicos não garantem RSS ou prazo universal. OOM, timeout ou execução não finalizada são **INCOMPLETE**, não uma conclusão estatística negativa.

## Verificações reproduzíveis

```sh
sh scripts/check.sh
```

A preparação baixa somente os artefatos oficiais exatos de `locks/artifacts.json`. Containers novos executam testes e consumo offline, sem montagem do host: UID/GID 1000, capacidades removidas, no-new-privileges, 256 PIDs, 1 GiB e 2 CPUs. Há 1.156 pares pequenos contra CDF independente em Fraction, referências Decimal com 70 dígitos, casos inválidos, limites e codecs reais. Um runtime separado consome o wheel sem checkout, ferramentas de build, pandas ou compilador, com raiz somente leitura e temporários limitados. Hashes e evidências reais ficam em `build/`.

Veja [API](docs/api.md), [contrato](docs/contract.pt-BR.md), [English](README.md) e [licenças](THIRD_PARTY.md). MIT cobre somente o código original Lofrano; dependências e bibliotecas nativas mantêm suas próprias licenças.
