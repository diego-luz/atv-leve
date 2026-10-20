# Como adicionar o perfil de uma TV

Um perfil diz ao ATV Leve quais apps **daquele modelo** podem ser desativados com
segurança. Ele só é útil se for feito com **dados reais**: nunca adicione nomes de
pacotes que você não viu na sua TV com `atvleve scan`.

## 1. Gere o perfil-modelo

```bash
atvleve info --ip IP_DA_TV
atvleve profile-export --ip IP_DA_TV
```

Isso cria `perfil-marca-modelo.yaml` com:

- `match`: como reconhecer a TV (o `ro.product.model` vem preenchido).
- `device_info`: Android, SDK, build e RAM (sem número de série).
- `inventory`: todos os apps encontrados e a classificação atual de cada um.
- `protected`, `safe_to_disable`, `confirm_first`: vazios, para você preencher.

## 2. Teste um app de cada vez

Para cada app que você acha que não serve para nada:

```bash
atvleve apply --ip IP_DA_TV --include nome.do.pacote --no-tune
```

Depois **reinicie a TV** e confira:

- [ ] A tela inicial aparece normalmente
- [ ] O controle remoto funciona (inclusive o botão de voz, se você usa)
- [ ] As entradas HDMI e a TV aberta (se houver) funcionam
- [ ] O Wi-Fi e o Bluetooth funcionam
- [ ] A Play Store abre e instala apps
- [ ] As configurações abrem
- [ ] Os apps que você usa (YouTube, Netflix, Globoplay...) abrem

Se algo quebrar: `atvleve restore --ip IP_DA_TV`.

## 3. Preencha as listas

| Lista | O que colocar |
|---|---|
| `protected` | Apps desta TV que **não podem** ser tocados e que o `_generic.yaml` não cobre (ex.: o app de imagem/som do fabricante). Aceita curingas (`com.fabricante.tv*`). |
| `safe_to_disable` | Só o que você **testou** como descrito acima. Nomes exatos. |
| `confirm_first` | O que provavelmente pode sair, mas depende do uso (ex.: assistente de voz, apps de canais). Nomes exatos. |

Apague a seção `inventory` antes de enviar (ou deixe; o ATV Leve ignora). Escreva em
`notes` o que você testou e o que quebrou.

## 4. Confira e envie

```bash
cp perfil-marca-modelo.yaml profiles/marca-modelo.yaml
atvleve scan --ip IP_DA_TV --profile marca-modelo   # confere a classificação
atvleve plan --ip IP_DA_TV                          # o perfil deve ser escolhido sozinho
pytest
```

Abra um pull request com o arquivo e uma descrição da TV (marca, modelo, polegadas,
RAM, Android e o resultado dos testes acima).

## Regras de revisão

- Um pacote que aparece em `protected` (do perfil ou do `_generic.yaml`) **sempre vence**:
  ele nunca é tocado, mesmo listado como seguro.
- `safe_to_disable` e `confirm_first` não aceitam curingas.
- Mudanças no `_generic.yaml` precisam de teste em `tests/test_protection.py`.
