# Plano de implementacao e implantacao

## Estado da versao 3.0.0

| Etapa | Entrega | Estado |
| --- | --- | --- |
| 1. Fontes opticas | Sentinel-2, Landsat, harmonizacao NASA HLS v2 L30/S30 e metadados. | Implementada no codigo-fonte. |
| 2. ECOSTRESS | Consulta CMR, download com token Earthdata e preparo LST/QC/cloud. | Implementada; download autenticado real requer validacao pelo usuario. |
| 3. CBERS e drone | CBERS TOA experimental, mosaico e indices de ortomosaico. | Implementada com limitacoes no PRD/TRD. |
| 4. Interface | Menu com dois submenus e nove algoritmos Processing. | Implementada. |
| 5. Documentacao/distribuicao | Tutorial, PRD, TRD, APPFLOW, backend e workflow de release. | Incluida nesta pasta. |
| 6. Regressao local | Suite em tests/test_satelite_cadente.py. | A execucao da versao 3.0.0 nesta preparacao permanece pendente. |
| 7. Instalacao em outro PC | Instalar ZIP de Release e executar fluxos com dados locais. | Pendente no computador de destino. |

## Liberacao no GitHub

1. Conferir que metadata.txt e a versao da tag sao iguais.
2. Criar/enviar uma tag SemVer, por exemplo v3.0.0.
3. O workflow .github/workflows/release.yml cria o ZIP com a pasta
   imagens_satellite na raiz e o anexa ao Release.
4. Baixar o ativo do Release; o ZIP de codigo-fonte do GitHub nao e instalador.

## Aceite em outro computador

1. No QGIS, instalar o ativo ZIP do Release e reiniciar o QGIS.
2. Confirmar menu, nove algoritmos e perfil ativo do QGIS.
3. Executar uma area pequena com HLS e conferir fonte, escala, Fmask,
   cobertura, resolucao e metadados.
4. Testar consulta publica ECOSTRESS; para download, configurar token
   Earthdata e autorizacao LP DAAC sem compartilhar a credencial.
5. Conferir GeoTIFF, QML, JSON, CRS, NoData e relatorio de cobertura.

## Riscos abertos

- HLS tem 30 m e nao substitui fontes de 10 m quando o detalhe espacial for
  essencial; disponibilidade temporal varia por area e nuvens.
- Download ECOSTRESS depende de acesso Earthdata/LP DAAC; a consulta publica
  pode retornar granulos parciais ou sem dados adequados.
- QA/cloud nao eliminam todos os erros de temperatura; comparar horarios e
  estacoes diferentes exige controle metodologico.
- Cobertura do retangulo nao garante cobertura valida dentro do poligono.

