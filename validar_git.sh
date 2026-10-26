#!/usr/bin/env bash
# Confere o que vai para o GitHub ANTES do commit. Não altera nada.
# Uso (no Git Bash, dentro da pasta do TCC):   bash validar_git.sh
# Resultado em validar_git.txt. Nunca mostra o conteúdo de uma linha com senha:
# só o arquivo, o número da linha e a palavra encontrada.

cd "$(dirname "$0")" || exit 1
SAIDA="validar_git.txt"
LISTA="$(mktemp)"
LIMITE=$((50 * 1024 * 1024))

# o que o "git add -A" colocaria no commit (sem colocar)
git -c core.quotepath=false add -A --dry-run 2>/dev/null \
  | sed -n "s/^add '\(.*\)'$/\1/p" > "$LISTA"

{
  echo "VALIDAÇÃO ANTES DO COMMIT — $(date '+%d/%m/%Y %H:%M')"
  echo

  echo "== 1. Arquivos que PRECISAM ficar de fora (cada um tem que aparecer com a regra)"
  for f in banco_config.ini handoff_tcc.zip cache_diario/ backup_*.csv; do
    [ -e "$f" ] || { echo "   (não existe) $f"; continue; }
    r=$(git check-ignore -v "$f" 2>/dev/null)
    if [ -n "$r" ]; then echo "   OK  $r"; else echo "   PROBLEMA: $f NÃO está ignorado"; fi
  done
  echo

  echo "== 2. Arquivos perigosos na lista do commit (tem que ficar vazio)"
  perig=$(grep -E '\.ini$|(^|/)backup_|\.zip$|^cache_diario/|\.env$' "$LISTA")
  if [ -z "$perig" ]; then echo "   OK  nenhum"; else echo "$perig" | sed 's/^/   PROBLEMA: /'; fi
  echo

  echo "== 3. Palavras de senha/credencial dentro dos arquivos que vão subir"
  echo "   (só arquivo:linha:palavra — o valor NÃO é mostrado)"
  achou=$(grep -v '^validar_git\.' "$LISTA" | tr '\n' '\0' \
    | xargs -0 grep -IinoE 'password|passwd|senha|pwd *=|locaweb|mysql\+pymysql://[^ ]*' 2>/dev/null \
    | sed -E 's#(mysql\+pymysql://)[^ ]*#\1***#')
  if [ -z "$achou" ]; then echo "   OK  nenhuma"; else echo "$achou" | sed 's/^/   VER: /'; fi
  echo

  echo "== 4. Arquivos acima de 50 MB (o GitHub recusa acima de 100 MB)"
  grandes=""
  while IFS= read -r f; do
    [ -f "$f" ] || continue
    t=$(wc -c < "$f")
    [ "$t" -gt "$LIMITE" ] && grandes="$grandes   $((t / 1024 / 1024)) MB  $f"$'\n'
  done < "$LISTA"
  if [ -z "$grandes" ]; then echo "   OK  nenhum"; else printf "%s" "$grandes"; fi
  echo

  echo "== 5. Resumo"
  echo "   arquivos novos/alterados que entram: $(wc -l < "$LISTA")"
  echo "   arquivos que saem do repositório:"
  git -c core.quotepath=false add -A --dry-run 2>/dev/null | sed -n "s/^remove '\(.*\)'$/      \1/p"
  total=0
  while IFS= read -r f; do [ -f "$f" ] && total=$((total + $(wc -c < "$f"))); done < "$LISTA"
  echo "   tamanho total a enviar: $((total / 1024 / 1024)) MB"
  echo

  echo "== 6. Para onde vai o push (usuário/token escondidos)"
  git remote -v | sed -E 's#//[^/@]*@#//***@#'
  echo
  echo "== 7. Lista completa do que entra"
  sed 's/^/   /' "$LISTA"
} > "$SAIDA" 2>&1

rm -f "$LISTA"
echo "Pronto. Abra o arquivo $SAIDA e me mande o conteúdo."
