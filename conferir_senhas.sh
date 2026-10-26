#!/usr/bin/env bash
# Mostra as linhas suspeitas COM OS VALORES MASCARADOS, para decidir se há
# senha escrita no código. Não altera nada.
# Uso (Git Bash, na pasta do TCC):   bash conferir_senhas.sh
# Resultado em conferir_senhas.txt.
#
# Máscara: todo texto entre aspas vira "***(N)" (N = quantidade de caracteres),
# exceto nomes de chave comuns (password, user, host...), que ficam visíveis
# para dar para ver se o valor vem de arquivo de configuração ou está escrito.

cd "$(dirname "$0")" || exit 1
SAIDA="conferir_senhas.txt"

mascarar() {
  perl -pe '
    s{(["\x27])((?:(?!\1).)*)\1}{
      my ($q,$v)=($1,$2);
      ($v eq "" || $v =~ /^(password|passwd|senha|user|usuario|host|port|database|db|banco|mysql|mqtt|wifi|ssid|broker|config|section|cfg)$/i
          || $v =~ /^[A-Z][A-Z0-9]*_(PASSWORD|PASS|PWD|SENHA|USER|HOST|PORT|DB|DATABASE|SSID|BROKER)$/)
        ? "$q$v$q" : "$q***(".length($v).")$q"
    }ge;
    s{(mysql\+pymysql://)\S*}{$1***}g;
  '
}

linhas() {   # arquivo, depois os números de linha
  local f="$1"; shift
  echo "---- $f"
  if [ ! -f "$f" ]; then echo "   (não existe)"; return; fi
  for n in "$@"; do
    printf "   %5s: " "$n"
    l=$(sed -n "${n}p" "$f" | cut -c1-220)
    if [ -z "$l" ]; then echo "(linha vazia ou inexistente)"; else printf "%s\n" "$l" | mascarar; fi
  done
}

{
  echo "LINHAS SUSPEITAS, COM VALORES MASCARADOS — $(date '+%d/%m/%Y %H:%M')"
  echo
  linhas Frame/firmware/mysql/mqtt_mysql.py 15 28 51 221
  linhas Frame/firmware/src/WifiManager.cpp 44
  linhas Frame/firmware/src/MqttPublisher.cpp 36
  linhas Frame/firmware/dashboard/index.html 36 37 39 40 45 65 74 75 76 533 729
  linhas Frame/firmware/dashboard/medias.html 436 437 440 465 497 740
  linhas banco.py 6 30 33 39 44 52 64
  linhas handoff/scripts.txt 35 38 44 49 57 69
  echo
  echo "---- o firmware tem config.h de verdade? está ignorado?"
  for f in Frame/firmware/include/config.h Frame/firmware/include/secrets.h; do
    if [ -e "$f" ]; then
      r=$(git check-ignore -v "$f" 2>/dev/null)
      echo "   $f existe — ${r:-PROBLEMA: NÃO está ignorado}"
    else
      echo "   $f não existe"
    fi
  done
  [ -f Frame/firmware/.gitignore ] && { echo "   Frame/firmware/.gitignore:"; sed 's/^/      /' Frame/firmware/.gitignore; }
  echo
  echo "---- arquivos acima de 50 MB (fora .git e cache_diario)"
  find . -path ./.git -prune -o -path ./cache_diario -prune -o -type f -size +50M -print
  echo "   (fim da lista)"
  echo
  echo "---- remoto (usuário/token escondidos)"
  git remote -v | sed -E 's#//[^/@]*@#//***@#'
} > "$SAIDA" 2>&1

echo "Pronto. Me mande o conteúdo de $SAIDA."
