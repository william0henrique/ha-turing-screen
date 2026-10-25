# Notas da versão — painel Turing + Home Assistant

## Novidades

O Home Assistant Core ganha um package YAML com controles para ligar/desligar o LCD, ajustar brilho, habilitar o fundo de fotos e entrar no modo descanso manual. O modo descanso mostra uma foto nítida com relógio e data, continua disponível por inatividade e coopera com eventos de suspensão e retomada do PC.

As barras de status das luzes usam as cores informadas pelo Home Assistant. O slideshow lê imagens da pasta `fotos/` no projeto Turing externo definido em `[tela] projeto`; o programa cria a pasta se necessário e corrige a orientação EXIF. O painel exibe avisos discretos para dispositivos monitorados que ficam indisponíveis, limitando a repetição por dispositivo. Entidades de câmeras e switches de privacidade são configurados localmente no INI, em vez de ficarem hardcoded no código.

O script `script.notificar_tela_turing` permite que automações enviem avisos com título e mensagem. Cada chamada pode personalizar cores, peso Roboto regular/negrito, tamanhos do título e do texto, posição, opacidade e duração. A documentação inclui um cartão Lovelace genérico para acrescentar à dashboard existente, sem publicar a dashboard pessoal inteira, além do guia de instalação e de um exemplo de INI sem credenciais. O intervalo de consulta é configurável, com `atualizar_seg = 2` como recomendação inicial.

## Segurança e configuração local

`ha_screen.ini` é um arquivo local e fica excluído pelo `.gitignore`. O arquivo público `ha_screen.example.ini` contém somente placeholders; copie-o para `ha_screen.ini` na sua máquina e preencha o URL do Home Assistant e um token válido. Nunca adicione credenciais ao Git ou aos arquivos anexados a uma release. Se um token for exposto, revogue-o e crie outro.

## Publicação

O repositório conectado ainda não tem tags Git nem releases; `v1.0.0` é uma sugestão para a primeira release formal. O workflow atual valida a sintaxe Python em push e pull request, mas não constrói um instalador Windows automaticamente. A release publicada inclui o snapshot do código-fonte do commit/tag.
