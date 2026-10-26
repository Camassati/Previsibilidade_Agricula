#include "MeshManager.h"
#include <esp_now.h>
#include <esp_wifi.h>
#include <WiFi.h>
#include "config.h"

static std::function<void(const String&)> onMeshMessageCallback = nullptr;
static uint8_t rootMac[6] = ROOT_MAC_ADDRESS;

static void onDataRecv(const uint8_t *mac, const uint8_t *incomingData, int len) {
  char buf[250];
  int copyLen = (len < (int)sizeof(buf) - 1) ? len : (int)sizeof(buf) - 1;
  memcpy(buf, incomingData, copyLen);
  buf[copyLen] = '\0';
  String msg(buf);

  Serial.printf("ESP-NOW recebido: %s\n", msg.c_str());

  if (IS_ROOT_NODE && onMeshMessageCallback) {
    onMeshMessageCallback(msg);
  }
}

static void onDataSent(const uint8_t *mac_addr, esp_now_send_status_t status) {
  Serial.print("ESP-NOW envio: ");
  Serial.println(status == ESP_NOW_SEND_SUCCESS ? "sucesso" : "falhou");
}

void MeshManager::begin() {
  WiFi.mode(WIFI_STA);

  if (!IS_ROOT_NODE) {
    // O no sensor nao se conecta a nenhum roteador - ele so precisa
    // que o radio esteja no MESMO CANAL que o no raiz vai usar
    // (ESP-NOW exige mesmo canal entre remetente e destinatario).
    // Depois que o no raiz conectar ao Wi-Fi pela primeira vez, ele
    // imprime o canal dele no Serial - copie esse numero para
    // ESPNOW_CHANNEL no config.h deste sensor.
    esp_wifi_set_channel(ESPNOW_CHANNEL, WIFI_SECOND_CHAN_NONE);
  }

  if (esp_now_init() != ESP_OK) {
    Serial.println("Erro ao iniciar ESP-NOW.");
    return;
  }

  esp_now_register_recv_cb(onDataRecv);
  esp_now_register_send_cb(onDataSent);

  if (!IS_ROOT_NODE) {
    esp_now_peer_info_t peerInfo = {};
    memcpy(peerInfo.peer_addr, rootMac, 6);
    peerInfo.channel = ESPNOW_CHANNEL;
    peerInfo.encrypt = false;

    if (esp_now_add_peer(&peerInfo) != ESP_OK) {
      Serial.println("Erro ao adicionar o no raiz como peer ESP-NOW.");
    }
    Serial.println("Este no esta configurado como SENSOR (ESP-NOW).");
  } else {
    Serial.println("Este no esta configurado como RAIZ (ESP-NOW + Wi-Fi/MQTT).");
    Serial.print("MAC deste no raiz (use em ROOT_MAC_ADDRESS dos sensores): ");
    Serial.println(WiFi.macAddress());
  }
}

void MeshManager::update() {
  // ESP-NOW e orientado a eventos (callbacks), nao precisa de
  // polling. Mantido vazio so por compatibilidade com main.cpp.
}

void MeshManager::broadcastReading(const String& jsonPayload) {
  esp_err_t result = esp_now_send(rootMac, (const uint8_t*)jsonPayload.c_str(), jsonPayload.length());
  if (result != ESP_OK) {
    Serial.println("Falha ao enviar via ESP-NOW.");
  }
}

void MeshManager::setOnMeshMessage(std::function<void(const String&)> callback) {
  onMeshMessageCallback = callback;
}
