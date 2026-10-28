# MVP de Controle de Acesso à Sala de Segurança

Protótipo em Flask e SQLite para gerenciar colaboradores e eventos de acesso RFID. A rota `/api/scan` recebe leituras e registra entradas, saídas, acessos negados ou alertas de tags desconhecidas. O painel web permite cadastrar, editar e remover colaboradores, acompanhar a ocupação e os eventos, exportar CSV e simular leituras.

## Estrutura do projeto

```text
app.py                    Rotas Flask, API e lógica do servidor
device.py                 Cliente Raspberry Pi, cache local e fila offline
templates/                Páginas HTML renderizadas pelo Flask
static/                   JavaScript e CSS do navegador
tests/                    Testes automatizados do servidor e do dispositivo
data/                     CSV demonstrativo e bancos de teste locais
analysis.ipynb            Análise dos eventos com Pandas
requirements.txt          Dependências do servidor e da análise
.gitignore                Arquivos locais ignorados pelo Git
```

Execute os testes a partir da raiz do projeto:

```sh
env/bin/python -m unittest discover -v
```

## Executar a aplicação

```sh
python3 -m venv env
. env/bin/activate
pip install -r requirements.txt
export SECRET_KEY='substitua-por-uma-chave-aleatoria'
export ADMIN_PASSWORD='escolha-uma-senha'
export DEVICE_TOKEN='um-token-longo-e-aleatorio'
HOST=0.0.0.0 python app.py
```

Acesse http://127.0.0.1:5000/login. Na primeira inicialização, a conta administrativa usa `ADMIN_USER` e `ADMIN_PASSWORD` (padrão: `admin` / `admin`). Configure essas variáveis antes do primeiro uso. O banco SQLite é criado como `access.db`; use `DATABASE` para escolher outro caminho.

`HOST=0.0.0.0` permite que outros dispositivos da rede local acessem o servidor Flask de desenvolvimento. Não o exponha diretamente à internet.

Após entrar, cadastre colaboradores pelo painel e teste as leituras no simulador. A primeira leitura autorizada registra uma entrada; a próxima registra a saída e a duração da visita. Tentativas negadas e tags desconhecidas também são registradas. O cliente embarcado mantém um cache local de permissões e uma fila persistente de eventos, sincronizada quando o servidor volta a ficar disponível. UUIDs tornam seguro reenviar eventos sem duplicá-los.

## Cliente Raspberry Pi

No Raspberry Pi, instale as bibliotecas de hardware compatíveis com o sistema (`gpiozero` e `mfrc522`). Defina `ACCESS_API` com o endereço do servidor na rede local, por exemplo `http://192.168.1.10:5000`, e configure o mesmo valor de `DEVICE_TOKEN` usado pelo servidor. Depois, execute:

```sh
python device.py
```

Para testar sem GPIO ou leitor RFID, execute `python device.py --simulate`. Os pinos BCM padrão são 17 para o LED verde, 27 para o LED vermelho e 22 para o buzzer; altere-os com `GREEN_LED_PIN`, `RED_LED_PIN` e `BUZZER_PIN`. As tags RFID são lidas como números decimais em texto. O banco local de cache e fila é `device.sqlite` por padrão; configure outro caminho com `DEVICE_DATABASE`.

No servidor, as rotas autenticadas para administração incluem `/api/employees`, `/api/events`, `/api/occupancy` e `/events.csv`. As rotas exclusivas do dispositivo, `/api/device/credentials` e `/api/device/events`, exigem o token bearer.

## Limitações do MVP

- A integração com o leitor RFID, LEDs e buzzer precisa ser calibrada e testada com os componentes e a fiação reais.
- O dispositivo valida acessos com as últimas permissões recebidas. Uma revogação pode levar até 60 segundos para chegar ao Raspberry Pi; eventos gerados sem conexão permanecem no dispositivo até uma sincronização bem-sucedida.
- O login administrativo é simples e usa uma única conta inicial. Altere a senha padrão e `SECRET_KEY` antes de qualquer uso compartilhado ou em rede.
- `analysis.ipynb` apresenta resultados demonstrativos com `data/sample_access_events.csv`. Para analisar dados reais, altere `DATA_FILE` para o CSV exportado pelo sistema, ajuste a data e a matrícula, execute todas as células e salve o notebook com os resultados.
