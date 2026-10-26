#pragma once

class WifiManager {
public:
  // So usado pelo no raiz - Wi-Fi normal (sem nenhuma "ponte"),
  // exatamente o fluxo ja testado e comprovado nesta rede antes do
  // mesh. Conecta e sincroniza a hora via NTP.
  static void connect();
  static bool isConnected();
};
