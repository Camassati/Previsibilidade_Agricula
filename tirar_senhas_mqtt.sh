#!/usr/bin/env bash
# Tira as duas senhas escritas em Frame/firmware/mysql/mqtt_mysql.py e as
# coloca em segredos.py, ao lado, que fica FORA do Git. O script continua
# funcionando igual (lê as senhas de segredos.py). Nada é mostrado na tela.
# Uso (Git Bash, na pasta do TCC):   bash tirar_senhas_mqtt.sh

cd "$(dirname "$0")" || exit 1
D="Frame/firmware/mysql"
F="$D/mqtt_mysql.py"
S="$D/segredos.py"

[ -f "$F" ] || { echo "não achei $F"; exit 1; }
if [ -f "$S" ]; then echo "$S já existe — não fiz nada para não sobrescrever."; exit 1; fi

n=$(grep -cE '^(MQTT_PASSWORD|MYSQL_PASSWORD)[[:space:]]*=' "$F")
if [ "$n" -ne 2 ]; then
  echo "esperava 2 linhas (MQTT_PASSWORD e MYSQL_PASSWORD) no começo da linha; achei $n. Não mexi em nada."
  exit 1
fi

# 1. cópia de segurança FORA da pasta do projeto (não corre risco de ir para o Git)
mkdir -p "$HOME/tcc_backup_senhas"
cp "$F" "$HOME/tcc_backup_senhas/mqtt_mysql.py.original"

# 2. as duas linhas, exatamente como estão, vão para segredos.py
{
  echo "# Senhas do coletor MQTT -> MySQL. NUNCA subir para o Git (está no .gitignore)."
  grep -E '^(MQTT_PASSWORD|MYSQL_PASSWORD)[[:space:]]*=' "$F"
} > "$S"

# 3. no script, cada linha vira um import do mesmo nome, no mesmo lugar
perl -i -pe 's/^(MQTT_PASSWORD|MYSQL_PASSWORD)\s*=.*$/from segredos import $1  # senha fora do Git (segredos.py)/' "$F"

# 4. .gitignore da raiz
grep -qxF "Frame/firmware/mysql/segredos.py" .gitignore || echo "Frame/firmware/mysql/segredos.py" >> .gitignore
grep -qxF "segredos.py" .gitignore || echo "segredos.py" >> .gitignore

# 5. conferências (sem mostrar valores)
echo "Linhas trocadas em $F:"
grep -nE '^from segredos import' "$F"
echo
echo "segredos.py está ignorado?"
git check-ignore -v "$S" || echo "PROBLEMA: segredos.py NÃO está ignorado"
echo
echo "Ainda sobra senha escrita no mqtt_mysql.py?"
grep -nE '(PASSWORD|SENHA)[[:space:]]*=[[:space:]]*r?["\x27]' "$F" | cut -d: -f1 | sed 's/^/   linha /' || true
[ -z "$(grep -nE '(PASSWORD|SENHA)[[:space:]]*=[[:space:]]*r?["\x27]' "$F")" ] && echo "   não"
echo
echo "O Python ainda entende o arquivo?"
(cd "$D" && python -c "import ast,sys; ast.parse(open('mqtt_mysql.py',encoding='utf-8').read()); print('   sim')") 2>&1
echo
echo "Original guardado em: $HOME/tcc_backup_senhas/mqtt_mysql.py.original"
