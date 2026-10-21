# Fase 2: trocar a tela inicial

Nas TVs com pouca RAM a tela inicial original (`com.google.android.tvlauncher` no
Android TV) costuma ser o maior consumidor de memória: ~208 MB numa Aiwa de 913 MB. Ela
também é a origem dos anúncios e recomendações.

## Como funciona hoje (`atvleve launcher`)

O ATV Leve **não instala nada**: você instala o launcher leve pela Play Store da TV
(recomendado: **FLauncher**, `me.efesser.flauncher`, código aberto) e abre uma vez.

```bash
atvleve top --ip 192.168.0.20                                 # anote o consumo do launcher
atvleve launcher --ip 192.168.0.20                            # lista as telas iniciais
atvleve launcher --ip 192.168.0.20 --set me.efesser.flauncher
```

Passos, todos com backup (tipo `launcher`) e conferência:

1. Checa a saúde da TV e se o pacote escolhido aparece como tela inicial (HOME).
2. Abre o novo launcher uma vez (`am start -W`) e exige que ele inicie de verdade.
3. `cmd package set-home-activity` para o novo launcher e confere que o Home abre nele.
4. Desativa `com.google.android.tungsten.setupwraith` (assistente de configuração).
   Precisa ser **antes** da tela original: com ele ativo, o `RecoveryActivity` dele toma
   o Home assim que a original sai (visto em TVs MT5867).
5. Desativa a tela original (`pm disable-user`, nunca desinstala).
6. Depois de cada passo (e redefine o padrão, porque o Android o apaga quando o conjunto de launchers muda) confere se o Home continua no novo launcher. Se não, reativa o
   que foi desativado e devolve o padrão para a tela original.
7. Mede a RAM livre antes e depois.

O painel faz o mesmo pelo botão **Trocar tela inicial** no relatório.

Esses dois pacotes continuam protegidos no `_generic.yaml`: o `apply` nunca mexe neles.
Só este fluxo os desativa, e só depois de ter um Home funcionando no lugar.

## Usando o launcher do cleanyourtv (`com.gathin.tvhome`)

Licença MIT, sem permissão de internet, APK de ~390 KB. Não há APK publicado: é preciso
compilar (Java 21 serve; no Debian 13 não existe mais o 17).

```bash
sudo apt install openjdk-21-jdk-headless
# Android SDK em ~/Android/Sdk: cmdline-tools + "platforms;android-34" "build-tools;34.0.0"
git clone --depth 1 https://github.com/Gathin23/cleanyourtv.git
cd cleanyourtv/launcher
echo "sdk.dir=$HOME/Android/Sdk" > local.properties
JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64 ./gradlew assembleRelease
```

Não rode o `build-and-install.sh` dele: o ATV Leve faz a instalação e a troca com backup.

```bash
atvleve launcher --ip 192.168.0.20 --install app/build/outputs/apk/release/app-release.apk
atvleve launcher --ip 192.168.0.20 --set com.gathin.tvhome
```

Sem a tela original, o botão **Input** do controle pode parar de abrir a troca de
entrada (HDMI). O launcher tem um serviço de acessibilidade opcional para isso
(`InputKeyService`), que só filtra teclas e não lê a tela:

```bash
atvleve launcher --ip 192.168.0.20 --set com.gathin.tvhome --input-key
```

O valor original de `enabled_accessibility_services` vai para o backup, os outros
serviços que a TV já tinha são mantidos e o `atvleve restore` volta tudo ao que era.

O serviço só reconhece as teclas `KEYCODE_TV` e `KEYCODE_TV_INPUT`. Em controles que
mandam outra coisa no botão de fonte ele não ajuda: numa AIWA AI PONT SA (MT5867,
receptor "MStar Smart TV IR Receiver") o botão manda `KEY_G` (veja
`examples/aiwa-ai-pont-sa` no cleanyourtv). Confira o seu antes da troca: com
`adb shell getevent -l`, aperte o botão e veja o código.

## Como desfazer

```bash
atvleve restore --ip 192.168.0.20
```

Reativa o assistente e a tela original e devolve o padrão para ela. O launcher novo
continua instalado; desinstale pela Play Store se quiser. Se o adb não estiver à mão:
Configurações → Apps → ver apps do sistema → reativar "Android TV Home".

## Ainda planejado: launcher próprio

Um launcher mínimo do projeto (Kotlin, Views simples, uma Activity, APK < 1 MB, lista
`LEANBACK_LAUNCHER`, relógio e atalho para Configurações). Só se o FLauncher não bastar
nas medições.
