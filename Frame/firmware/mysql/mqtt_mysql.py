import json
import time
import paho.mqtt.client as mqtt
import mysql.connector


# ============================================================
# CONFIGURAÇÕES MQTT - HIVEMQ
# ============================================================

MQTT_HOST = "f9a3dd50d01446e6a05d70d45d99e036.s1.eu.hivemq.cloud"
MQTT_PORT = 8883

MQTT_USERNAME = "hivemq.webclient.1789241531866"
from segredos import MQTT_PASSWORD  # senha fora do Git (segredos.py)

MQTT_TOPIC = "calculadora_agricola/leituras"


# ============================================================
# CONFIGURAÇÕES MYSQL
# ============================================================

MYSQL_HOST = "tcc2026.mysql.dbaas.com.br"
MYSQL_PORT = 3306
MYSQL_DATABASE = "tcc2026"
MYSQL_USER = "tcc2026"
from segredos import MYSQL_PASSWORD  # senha fora do Git (segredos.py)


# ============================================================
# ID DA CONFIGURAÇÃO
# ============================================================

ID_CONFIG = 1


# ============================================================
# CONEXÃO COM MYSQL
# ============================================================

def conectar_mysql():

    try:

        conexao = mysql.connector.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            database=MYSQL_DATABASE,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD
        )

        print("MySQL conectado com sucesso!")

        return conexao

    except mysql.connector.Error as erro:

        print("Erro ao conectar no MySQL:")
        print(erro)

        return None


# ============================================================
# QUANDO CONECTAR AO MQTT
# ============================================================

def on_connect(client, userdata, flags, rc, properties=None):

    if rc == 0:

        print()
        print("========================================")
        print("MQTT conectado com sucesso!")
        print("Broker:", MQTT_HOST)
        print("Tópico:", MQTT_TOPIC)
        print("========================================")
        print()

        client.subscribe(MQTT_TOPIC)

        print("Aguardando leituras do ESP32...")

    else:

        print("Erro ao conectar no MQTT.")
        print("Código:", rc)


# ============================================================
# QUANDO RECEBER UMA MENSAGEM MQTT
# ============================================================

def on_message(client, userdata, message):

    try:

        # Converte o payload recebido para texto
        payload = message.payload.decode("utf-8")

        print()
        print("========================================")
        print("NOVA LEITURA RECEBIDA")
        print("========================================")
        print("Payload:", payload)

        # Converte o JSON
        dados = json.loads(payload)

        # ====================================================
        # DADOS ENVIADOS PELO ESP32
        # ====================================================

        id_sensor = 0
        temperatura = dados["temperatura"]
        umidade = dados["umidade"]
        data_hora = dados["hora"]

        print("Sensor:", id_sensor)
        print("Temperatura:", temperatura)
        print("Umidade:", umidade)
        print("Data/Hora:", data_hora)

        # ====================================================
        # CONECTA AO MYSQL
        # ====================================================

        conexao = conectar_mysql()

        if conexao is None:

            print("Não foi possível inserir a leitura.")
            return

        cursor = conexao.cursor()

        # ====================================================
        # INSERT
        # ====================================================

        sql = """
            INSERT INTO LEITURA_IOT
            (
                id_config,
                id_sensor,
                temperatura,
                umidade_relativa,
                data_hora
            )
            VALUES
            (
                %s,
                %s,
                %s,
                %s,
                %s
            )
        """

        valores = (
            ID_CONFIG,
            id_sensor,
            temperatura,
            umidade,
            data_hora
        )

        cursor.execute(sql, valores)

        conexao.commit()

        print()
        print(">>> LEITURA SALVA NO MYSQL COM SUCESSO!")
        print("ID Configuração:", ID_CONFIG)
        print("ID Sensor:", id_sensor)
        print("Temperatura:", temperatura)
        print("Umidade:", umidade)
        print("Data/Hora:", data_hora)
        print("========================================")

        cursor.close()
        conexao.close()

    except json.JSONDecodeError:

        print()
        print("ERRO: O payload recebido não é um JSON válido.")
        print("Payload:", message.payload.decode("utf-8"))

    except KeyError as erro:

        print()
        print("ERRO: Campo não encontrado no JSON:", erro)

    except mysql.connector.Error as erro:

        print()
        print("ERRO AO INSERIR NO MYSQL:")
        print(erro)

    except Exception as erro:

        print()
        print("ERRO INESPERADO:")
        print(erro)


# ============================================================
# CONFIGURAÇÃO DO CLIENTE MQTT
# ============================================================

client = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION2,
    client_id="mysql_inserter"
)

client.username_pw_set(
    MQTT_USERNAME,
    MQTT_PASSWORD
)

# TLS/SSL
client.tls_set()

# Eventos MQTT
client.on_connect = on_connect
client.on_message = on_message


# ============================================================
# CONEXÃO
# ============================================================

print("========================================")
print("SISTEMA MQTT -> MYSQL")
print("========================================")

print("Conectando ao HiveMQ...")
print("Broker:", MQTT_HOST)
print("Porta:", MQTT_PORT)

try:

    client.connect(
        MQTT_HOST,
        MQTT_PORT,
        60
    )

    print("Conexão iniciada.")
    print("Aguardando mensagens...")
    print()

    # Mantém o programa rodando
    client.loop_forever()

except Exception as erro:

    print()
    print("ERRO AO CONECTAR AO MQTT:")
    print(erro)