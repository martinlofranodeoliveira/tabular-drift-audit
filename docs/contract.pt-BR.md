# Contrato

KS calcula a maior diferença absoluta entre CDFs empíricas, após avançar todos os empates de ambos os lados. Produtos inteiros usam denominador comum no máximo `2^28` e a fração final é reduzida. Null/NaN numéricos são ausência; infinito e módulo acima de `1e12` são inválidos. Int32/int64 nesse domínio convertem exatamente para float64; zeros com sinais diferentes são iguais. Não há valor-p ou teste assintótico.

PSI usa intervalos `(-∞,c0], (c0,c1], ..., (c_final,+∞)`, sem ajustar os cortes aos dados atuais. Para K intervalos: `p_i=(r_i+1/2)/(n_ref+K/2)`, `q_i=(c_i+1/2)/(n_atual+K/2)` e `PSI=Σ(p_i-q_i)ln(p_i/q_i)`. A procedência dos cortes fornecidos não é comprovada. A pseudocontagem depende do tamanho da amostra: `[1,3]` contra `[2,6]` dá `ln(39/35)/45`, não zero.

JSD usa toda a união categórica observada, sem suavização, mistura com pesos iguais e logaritmos base 2, com termos zero definidos como zero. O resultado é a divergência em `[0,1]`. Não há normalização Unicode. Null é ausência; string vazia e `NaN` textual são categorias. Taxas de ausência são frações exatas sobre todas as linhas; a diferença é atual menos referência.

A precedência das distribuições é `UNAVAILABLE_SCHEMA`, `INSUFFICIENT_NONMISSING`, `AVAILABLE`. Campo ausente não vira uma coluna artificial de nulls. Taxas de campos presentes continuam calculadas pelo tipo real, mesmo quando a distribuição é incompatível com o plano. Ordem dos campos e metadados comuns são ignorados. Índices de dicionários não identificam categorias. Mudanças entre representações textuais ou entre int32/int64/float64 podem ser compatíveis.

## Limites

Arquivo: 8 MiB; plano: 64 KiB; cabeçalho físico CSV: 4096 bytes. Cada lado: 16.384 linhas e 32 campos, com até 16 selecionados. Nomes: 64 bytes ASCII; todos os valores/dicionários textuais: 256 bytes UTF-8. União observada de cada campo textual selecionado: 128 categorias; níveis declarados pandas: 128. Entradas de dicionário por campo somadas nos chunks: 16.384. Cortes/intervalos: 15/16.

Parquet: 64 grupos, 32 MiB descomprimidos declarados; Thrift: 64 KiB por string e 4096 entradas por container. Lotes: 256 linhas/4 MiB selecionados; admissão total selecionada: 64 MiB. Visitas originais: 2.097.152; passos KS distintos: 524.288; JSON: 8 MiB. `Budget` permite somente reduzir limites com inteiros positivos sem bool.

Um contador compartilhado inclui cabeçalhos de chunks, células inclusive null, entradas de dicionário inclusive não usadas e mapeamentos selecionados. Chunks vazios são cobrados antes da travessia. Repetições de dicionários são contadas conservadoramente. A CLI valida todos os campos, descarta dados extras e conserva os selecionados para o mesmo núcleo estatístico. Representação física afeta contadores/admissão conservadora, não fornece medição exata do alocador nativo.

## Arquivos e extensões

São suportados diretórios locais Linux controlados e cooperativos, com leitura estável e sem alteração hostil concorrente. A folha é aberta com NONBLOCK/NOFOLLOW/CLOEXEC; fstat exige arquivo regular antes da leitura. O leitor mantém no máximo limite mais sentinela e continua até EOF. Não há promessa para pais hostis, bloqueio de stdout herdado, durabilidade após falha de energia ou limpeza universal de processos. Relatórios persistentes não são sobrescritos pela aplicação.

CSV tem tipos explícitos para todos os campos, largura estrita, linhas vazias preservadas e sem registros/cabeçalhos citados multilinha ou compressão. Somente vazio não citado vira null. O parser CSV da biblioteca Python lê o cabeçalho; Arrow lê valores. Tipos CSV são declarados pelo plano; token numérico malformado é erro de entrada, não evidência de mudança inferida.

Parquet admite apenas UNCOMPRESSED/SNAPPY/ZSTD e tipos planos declarados. Metadados são limitados antes dos lotes; checksums de páginas são verificados quando presentes. Referências externas, tipos aninhados, criptografados, extensões e outros codecs são rejeitados. O esquema de origem é capturado antes da conversão interna de leitura para dicionário.

Leitores de arquivos são privados à CLI nova e controlada, sem pandas, plugins, scripts ou registros arbitrários de extensões. `arrow_extensions_enabled=False` não bloqueia universalmente callbacks genéricos: extensão registrada pode desserializar no construtor `ParquetFile`. No processo suportado, metadados desconhecidos/customizados/legados são mantidos como armazenamento e rejeitados antes dos lotes. PyArrow 26.0.1 não oferece o carregador público/lib `PyExtensionType` com autopickle. Registries pré-carregados arbitrários estão fora do contrato; `ARROW:schema` comum não é rejeitado por si só.

O entrypoint instalado normal confia no estado de inicialização: `PYTHONPATH`, user-site, `sitecustomize` e pacotes instalados precisam ser controlados. `-I -m` é outro modo exercitado, não um sandbox de registry. Callback zero só foi estabelecido naquele interpretador novo e próprio com os pacotes qualificados. O callback no contexto registrado é um contraexemplo explícito fora desse escopo. Tabelas/DataFrames públicos do SDK já foram criados por um chamador Python confiável; não há alegação sobre callbacks durante sua criação.

Parsers, descompressão e conversão pandas podem alocar antes das verificações posteriores. Containers de teste usam 1 GiB/2 CPUs; crash, OOM, timeout, dependência/oráculo ausente ou execução não finalizada são **INCOMPLETE**, não orçamento lógico ou conclusão negativa. `UNKNOWN_BUDGET` não entrega relatório parcial como completo. Dados malformados são `INVALID_INPUT`, recursos não admitidos `UNSUPPORTED_INPUT`, resultados numericamente injustificáveis `NUMERICALLY_INDETERMINATE`. Só se admite ajuste de arredondamento a até `1e-14` de extremo comprovado.

O relatório é construído e serializado privadamente antes do retorno. Stdout não é atômico: o consumidor exige documento inteiro válido, `complete=true` e saída 0. Falha de I/O pode truncar a saída e impede aceitar completude.
