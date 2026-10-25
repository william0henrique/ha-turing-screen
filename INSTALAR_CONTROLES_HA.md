# Tela Turing e notificações no Home Assistant Core

O package `turing_screen.yaml` cria `light.tela_turing` para ligar/desligar o LCD e ajustar o brilho, `switch.fundo_fotos_tela` para o slideshow e `switch.modo_descanso_tela_turing` para mostrar somente a foto nítida, o relógio e a data. Também cria o script reutilizável `script.notificar_tela_turing`, que pode ser chamado por qualquer automação.

## Instalar ou atualizar no Home Assistant

1. Faça uma cópia de segurança do `configuration.yaml`.
2. Na pasta de configuração do Home Assistant, crie `packages` se ainda não existir. Copie o novo `turing_screen.yaml` para essa pasta, substituindo o anterior.
3. No `configuration.yaml`, habilite packages:

   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```

   Se já existir `homeassistant:`, não a duplique: acrescente `packages:` dentro da seção existente. Se packages já estiver configurado, mantenha a diretiva atual e use a pasta que ela inclui.
4. Valide a configuração e reinicie o Home Assistant Core para carregar as entidades e o script.

## Adicionar o controle à dashboard

O projeto inclui um exemplo compacto em `docs/examples/dashboard-card-tela-turing.yaml`. Adicione esse cartão à sua dashboard existente; ele não substitui nem altera o restante da dashboard. O cartão usa as entidades padrão `light.tela_turing`, `switch.fundo_fotos_tela` e `switch.modo_descanso_tela_turing`.

## Usar notificações em automações

Na automação, adicione a ação **Executar script** e escolha **Notificar tela Turing**. `title` e `message` bastam; os demais campos são opcionais:

```yaml
action: script.notificar_tela_turing
data:
  title: "Portão"
  message: "O portão ficou aberto."
  color: "#FF9800"
  background_color: "#171923"
  title_color: "#FFFFFF"
  message_color: "#FFE8C2"
  font: bold
  title_size: 18
  message_size: 14
  duration: 12
  position: bottom
  opacity: 90
```

`color` muda a borda e o detalhe lateral; `background_color`, `title_color` e `message_color` controlam as outras cores. Aceitam cores hexadecimais ou nomes CSS, como `red`. `font` pode ser `regular` ou `bold` usando as fontes Roboto do projeto. `title_size` aceita 10–24 px; `message_size`, 9–20 px. `duration` aceita 1–60 segundos. `position` pode ser `top` ou `bottom`; `opacity` vai de 20 a 100%. Cada novo aviso substitui o que ainda estiver visível.

Dentro de uma automação, coloque o mesmo bloco na lista `actions`:

```yaml
actions:
  - action: script.notificar_tela_turing
    data:
      title: "Portão"
      message: "O portão ficou aberto."
      color: "#FF9800"
      duration: 12
```

## Configuração local do painel

Copie `ha_screen.example.ini` para `ha_screen.ini` na máquina do painel. Edite o URL do Home Assistant, insira um token válido e ajuste `[tela] projeto` para a pasta separada do projeto original `turing-smart-screen-python` — ela precisa conter `library/` e `res/`. Dentro de `[ha]`, `atualizar_seg = 2` é uma recomendação inicial. Salve e reinicie o programa.

O slideshow procura as imagens em `fotos/` dentro da pasta informada em `[tela] projeto` e cria essa pasta automaticamente se ela não existir. Coloque ali as fotos que deseja exibir.

O `ha_screen.ini` pode conter credenciais e fica excluído pelo `.gitignore`; não o adicione ao Git. O exemplo público usa somente placeholders. Para monitorar câmeras, informe entidades na seção `[entidades]`, por exemplo:

```ini
[entidades]
cameras = camera.portao=Campainha, camera.garagem=Câmera garagem
camera_privacidade = camera.portao=switch.portao_privacy_mode
```

A lista de privacidade é opcional; quando o respectivo switch está `on`, o modo de privacidade não gera um falso aviso de indisponibilidade. Os IDs acima são exemplos: use os da sua instalação apenas no INI local.

O protetor automático por inatividade continua funcionando. O modo descanso manual é um toggle e não suspende o Windows nem apaga o LCD. Se um token for exposto ou incluído em um commit, revogue-o no Home Assistant e crie outro.
