#pragma once
#include <Arduino.h>
#include <functional>

class MeshManager {
public:
  static void begin();
  static void update();  // mantido por compatibilidade; ESP-NOW nao precisa de polling, mas nao faz mal chamar
  static void broadcastReading(const String& jsonPayload);

  // So tem efeito no no raiz - registra quem trata mensagens
  // recebidas de outros nos, para repassar ao MQTT.
  static void setOnMeshMessage(std::function<void(const String&)> callback);
};
