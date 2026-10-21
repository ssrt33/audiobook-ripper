#!/bin/sh
# Runs the app as PUID:PGID (TrueNAS "apps" = 568) with access to the optical drive(s).
set -e
PUID="${PUID:-568}"
PGID="${PGID:-568}"
DRIVES="${DRIVES:-/dev/sr0}"

mkdir -p "${WORK_DIR:-/work}" "${CONFIG_DIR:-/config}"
# Also mark the work dir for Audiobookshelf's full scans. Its live watcher ignores this, so the
# work dir should still sit outside the library (the app warns at startup if it doesn't).
touch "${WORK_DIR:-/work}/.ignore" 2>/dev/null || true
chown "$PUID:$PGID" "${WORK_DIR:-/work}" "${CONFIG_DIR:-/config}" 2>/dev/null || true

# Collect the group ids that own the drive devices (and their /dev/sg* twins) so we can read them.
SUPP_GIDS="$PGID"
for dev in $(echo "$DRIVES" | tr ',' ' ') /dev/sg*; do
  if [ -e "$dev" ]; then
    gid=$(stat -c %g "$dev")
    case ",$SUPP_GIDS," in *",$gid,"*) ;; *) SUPP_GIDS="$SUPP_GIDS,$gid" ;; esac
  fi
done

if [ "$(id -u)" = "0" ] && [ "$PUID" != "0" ]; then
  exec setpriv --reuid="$PUID" --regid="$PGID" --groups="$SUPP_GIDS" --inh-caps=-all \
       python -m ripper
fi
exec python -m ripper
