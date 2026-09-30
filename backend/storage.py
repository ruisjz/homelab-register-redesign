import contextlib, json, logging, sqlite3, os, threading, time, uuid, zipfile, hashlib, shutil, re
from pathlib import Path
from datetime import date, datetime, timezone
from .config import DATA_DIR, DB_PATH, SEED_PATH, AUTO_BACKUPS_KEEP

log = logging.getLogger("homelab-register")

COMPONENT_CATEGORIES = {
    "cpu",
    "gpu",
    "motherboard",
    "ram",
    "storage",
    "psu",
    "network",
    "cooler",
    "fan",
    "controller",
    "other",
}

LEGACY_COMPONENT_TYPE_MAP = {
    "cpu": "cpu",
    "processor": "cpu",
    "gpu": "gpu",
    "igpu": "gpu",
    "motherboard": "motherboard",
    "ram": "ram",
    "ddr3": "ram",
    "ddr4": "ram",
    "ddr5": "ram",
    "hdd": "storage",
    "ssd": "storage",
    "ssd nvme": "storage",
    "ssd sata": "storage",
    "nvme": "storage",
    "storage": "storage",
    "disk": "storage",
    "disco": "storage",
    "ethernet": "network",
    "nic": "network",
    "network": "network",
    "psu": "psu",
    "power supply": "psu",
    "cooler": "cooler",
    "cooling": "cooler",
    "fan": "fan",
    "ventoinha": "fan",
    "controller": "controller",
    "controlador": "controller",
    "other": "other",
    "outro": "other",
}


def normalize_component_type(value):
    raw = str(value or "").strip().lower()
    if raw in COMPONENT_CATEGORIES:
        return raw
    return LEGACY_COMPONENT_TYPE_MAP.get(raw, "other")


SCHEMA = """
CREATE TABLE IF NOT EXISTS equipment(id TEXT PRIMARY KEY,sort_order INTEGER NOT NULL DEFAULT 0,name TEXT NOT NULL DEFAULT '',hostname TEXT NOT NULL DEFAULT '',role TEXT NOT NULL DEFAULT '',vendor TEXT NOT NULL DEFAULT '',model TEXT NOT NULL DEFAULT '',serial TEXT NOT NULL DEFAULT '',location TEXT NOT NULL DEFAULT '',created TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'ok',cpu TEXT NOT NULL DEFAULT '',motherboard TEXT NOT NULL DEFAULT '',gpu TEXT NOT NULL DEFAULT '',nic TEXT NOT NULL DEFAULT '',psu TEXT NOT NULL DEFAULT '',bios TEXT NOT NULL DEFAULT '',notes TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS components(id TEXT PRIMARY KEY,equipment_id TEXT NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,sort_order INTEGER NOT NULL DEFAULT 0,name TEXT NOT NULL DEFAULT '',type TEXT NOT NULL DEFAULT '',model TEXT NOT NULL DEFAULT '',serial TEXT NOT NULL DEFAULT '',capacity TEXT NOT NULL DEFAULT '',health TEXT NOT NULL DEFAULT '',retired INTEGER NOT NULL DEFAULT 0,previous_equipment_ids TEXT NOT NULL DEFAULT '[]',technology TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS maintenance(id TEXT PRIMARY KEY,equipment_id TEXT NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,date TEXT NOT NULL DEFAULT '',type TEXT NOT NULL DEFAULT '',description TEXT NOT NULL DEFAULT '',areas TEXT NOT NULL DEFAULT '',notes TEXT NOT NULL DEFAULT '',results_json TEXT NOT NULL DEFAULT '{}');
-- Legacy table kept so existing databases can be migrated without losing data.
CREATE TABLE IF NOT EXISTS maintenance_plans(id TEXT PRIMARY KEY,equipment_id TEXT REFERENCES equipment(id) ON DELETE CASCADE,sort_order INTEGER NOT NULL DEFAULT 0,name TEXT NOT NULL DEFAULT '',interval_months INTEGER NOT NULL DEFAULT 6,active INTEGER NOT NULL DEFAULT 1,notes TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS preventive_plans(id TEXT PRIMARY KEY,sort_order INTEGER NOT NULL DEFAULT 0,name TEXT NOT NULL DEFAULT '',interval_months INTEGER NOT NULL DEFAULT 6,active INTEGER NOT NULL DEFAULT 1,notes TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS preventive_plan_equipment(plan_id TEXT NOT NULL REFERENCES preventive_plans(id) ON DELETE CASCADE,equipment_id TEXT NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,PRIMARY KEY(plan_id,equipment_id));
CREATE TABLE IF NOT EXISTS maintenance_checks(maintenance_id TEXT NOT NULL REFERENCES maintenance(id) ON DELETE CASCADE,check_name TEXT NOT NULL,checked INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(maintenance_id,check_name));
CREATE TABLE IF NOT EXISTS app_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS attachments(id TEXT PRIMARY KEY,owner_type TEXT NOT NULL,owner_id TEXT NOT NULL,original_name TEXT NOT NULL,display_name TEXT NOT NULL DEFAULT '',category TEXT NOT NULL DEFAULT 'other',mime_type TEXT NOT NULL DEFAULT 'application/octet-stream',size INTEGER NOT NULL DEFAULT 0,stored_name TEXT NOT NULL UNIQUE,created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_attachments_owner ON attachments(owner_type,owner_id);
CREATE INDEX IF NOT EXISTS idx_attachments_category ON attachments(owner_type,owner_id,category);
"""


def now():
    return datetime.now(timezone.utc).isoformat()


SCHEMA_VERSION = 3

# P15: todo o conteúdo de ficheiros circula em blocos de 1 MiB; nunca um ficheiro inteiro em RAM.
CHUNK = 1024 * 1024
MAX_ATTACHMENT_SIZE = 25 * 1024 * 1024
MAX_SNAPSHOT_SIZE = 512 * 1024 * 1024
MAX_MANIFEST_SIZE = 16 * 1024 * 1024
MAX_SNAPSHOT_DB_SIZE = 2 * 1024 * 1024 * 1024
DISK_MARGIN = 32 * 1024 * 1024


def tmp_dir():
    """Temporários sempre em data/tmp/ (disco), nunca em /tmp, que pode ser memória."""
    d = DATA_DIR / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_tmp_path(prefix, suffix=""):
    return tmp_dir() / f"{prefix}-{uuid.uuid4().hex}{suffix}"


def cleanup_tmp():
    """No arranque: nenhuma operação sobrevive a um reinício, por isso data/tmp/ é esvaziado."""
    removed = 0
    for p in tmp_dir().iterdir():
        try:
            shutil.rmtree(p) if p.is_dir() else p.unlink()
            removed += 1
        except OSError as e:
            log.warning("Não foi possível apagar o temporário %s: %s", p.name, e)
    if removed:
        log.info("Temporários de uma execução anterior apagados: %s", removed)


def copy_stream(src, dst, limit=None, hasher=None):
    """Copia em blocos de CHUNK. Com limit, recusa se a origem tiver mais bytes do que o previsto."""
    total = 0
    while True:
        chunk = src.read(CHUNK)
        if not chunk:
            return total
        total += len(chunk)
        if limit is not None and total > limit:
            raise ValueError("O ficheiro tem mais dados do que o declarado.")
        if hasher is not None:
            hasher.update(chunk)
        dst.write(chunk)


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def ensure_free_space(needed, where=None):
    free = shutil.disk_usage(where or DATA_DIR).free
    if free < needed + DISK_MARGIN:
        raise ValueError(
            f"Espaço em disco insuficiente: são precisos cerca de {(needed + DISK_MARGIN) // (1024 * 1024)} MB "
            f"e há {free // (1024 * 1024)} MB livres."
        )


class BusyError(Exception):
    pass


_heavy_lock = threading.Lock()


@contextlib.contextmanager
def heavy_operation():
    """P15: só um backup, validação ou restore de cada vez (memória e disco previsíveis)."""
    if not _heavy_lock.acquire(blocking=False):
        raise BusyError("Já há um backup ou restore em curso. Tenta daqui a pouco.")
    try:
        yield
    finally:
        _heavy_lock.release()


class RestoreInProgressError(BusyError):
    pass


# B16 (P30): o restore troca os ficheiros da BD e dos anexos. Durante essa troca, nenhum
# pedido lê ou grava o estado: esperam até STATE_LOCK_WAIT segundos e, se o restore ainda
# não acabou, recebem RestoreInProgressError (503) em vez de escreverem num ficheiro que
# vai ser substituído. Os pedidos normais também passam por aqui, um de cada vez, o que
# é desprezável para uma app de um utilizador.
_state_lock = threading.RLock()
STATE_LOCK_WAIT = float(os.getenv("STATE_LOCK_WAIT", "5"))  # configurável só para testes


@contextlib.contextmanager
def state_access():
    if not _state_lock.acquire(timeout=STATE_LOCK_WAIT):
        raise RestoreInProgressError("Está a decorrer um restore. Tenta de novo daqui a pouco.")
    try:
        yield
    finally:
        _state_lock.release()


def _connect(path):
    c = sqlite3.connect(path, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def db(path=None):
    """Abre uma ligação. P17: já não cria o esquema nem corre migrações em cada pedido;
    isso acontece uma vez, no arranque (init)."""
    if path is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = DB_PATH
    return _connect(path)


def schema_version(c):
    return c.execute("PRAGMA user_version").fetchone()[0]


def _schema_statements():
    buf = ""
    for line in SCHEMA.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                yield buf
            buf = ""


def _migration_1(c):
    """Versão 1: o esquema atual e as migrações que antes corriam em cada pedido."""
    for statement in _schema_statements():
        c.execute(statement)
    cols = {r["name"] for r in c.execute("PRAGMA table_info(maintenance)").fetchall()}
    if "results_json" not in cols:
        c.execute("ALTER TABLE maintenance ADD COLUMN results_json TEXT NOT NULL DEFAULT '{}'")
        if "results_legacy_json" in cols:
            c.execute("UPDATE maintenance SET results_json=results_legacy_json WHERE results_json='{}'")
    migrate_legacy_plans(c)
    for row in c.execute("SELECT id,type FROM components").fetchall():
        normalized = normalize_component_type(row["type"])
        if normalized != row["type"]:
            c.execute("UPDATE components SET type=? WHERE id=?", (normalized, row["id"]))


def _migration_2(c):
    """Versão 2 (P22): componentes retirados e histórico das máquinas por onde passaram."""
    cols = {r["name"] for r in c.execute("PRAGMA table_info(components)").fetchall()}
    if "retired" not in cols:
        c.execute("ALTER TABLE components ADD COLUMN retired INTEGER NOT NULL DEFAULT 0")
    if "previous_equipment_ids" not in cols:
        c.execute("ALTER TABLE components ADD COLUMN previous_equipment_ids TEXT NOT NULL DEFAULT '[]'")


RAM_TECH_RE = re.compile(r"\b((?:LP)?DDR\s?\d[A-Z]?)\b", re.I)


def _ram_technology(text):
    """Tipo de RAM se o texto indicar exatamente um (ex.: «16GB DDR4» -> DDR4)."""
    found = {m.group(1).upper().replace(" ", "") for m in RAM_TECH_RE.finditer(text or "")}
    return found.pop() if len(found) == 1 else ""


def _storage_technology(text):
    """Tipo de armazenamento se o texto indicar exatamente um (ex.: «NVMe 1TB» -> NVMe)."""
    t = (text or "").lower()
    found = set()
    if "nvme" in t:
        found.add("NVMe")
    if "emmc" in t:
        found.add("eMMC")
    if re.search(r"\bsas\b", t):
        found.add("SAS")
    if re.search(r"\bhdd\b", t):
        found.add("HDD")
    if re.search(r"\bsata\b", t) and re.search(r"\bssd\b", t):
        found.add("SATA SSD")
    return found.pop() if len(found) == 1 else ""


def _migration_3(c):
    """Versão 3 (P23): campo «Tecnologia» nos componentes; RAM e armazenamento do equipamento
    passam a ser calculados a partir dos componentes e os campos manuais deixam de existir.

    A tecnologia só é preenchida quando é clara: pelo nome/modelo do próprio componente,
    ou pelo texto antigo do equipamento (RAM: todos os módulos; armazenamento: só se houver
    um único dispositivo). Nos restantes casos fica em branco. O snapshot pré-migração
    guarda os textos antigos.
    """
    cols = {r["name"] for r in c.execute("PRAGMA table_info(components)").fetchall()}
    if "technology" not in cols:
        c.execute("ALTER TABLE components ADD COLUMN technology TEXT NOT NULL DEFAULT ''")
    detect = {"ram": _ram_technology, "storage": _storage_technology}
    for comp in c.execute("SELECT id,type,name,model FROM components WHERE technology=''").fetchall():
        if comp["type"] in detect:
            tech = detect[comp["type"]](f"{comp['name']} {comp['model']}")
            if tech:
                c.execute("UPDATE components SET technology=? WHERE id=?", (tech, comp["id"]))
    eq_cols = {r["name"] for r in c.execute("PRAGMA table_info(equipment)").fetchall()}
    if "ram" in eq_cols and "storage" in eq_cols:
        for e in c.execute("SELECT id,ram,storage FROM equipment").fetchall():
            tech = _ram_technology(e["ram"])
            if tech:
                c.execute(
                    "UPDATE components SET technology=? WHERE equipment_id=? AND type='ram' AND technology=''",
                    (tech, e["id"]),
                )
            tech = _storage_technology(e["storage"])
            devices = c.execute(
                "SELECT id,technology FROM components WHERE equipment_id=? AND type='storage' AND retired=0",
                (e["id"],),
            ).fetchall()
            if tech and len(devices) == 1 and not devices[0]["technology"]:
                c.execute("UPDATE components SET technology=? WHERE id=?", (tech, devices[0]["id"]))
    for column in ("ram", "storage"):
        if column in eq_cols:
            c.execute(f"ALTER TABLE equipment DROP COLUMN {column}")


MIGRATIONS = [(1, _migration_1), (2, _migration_2), (3, _migration_3)]


class SchemaTooNewError(RuntimeError):
    pass


def migrate(c):
    """Aplica as migrações em falta numa única transação (P17). Devolve True se migrou."""
    version = schema_version(c)
    if version > SCHEMA_VERSION:
        raise SchemaTooNewError(
            f"A base de dados é de uma versão mais recente da aplicação (esquema v{version}; "
            f"esta versão só conhece até v{SCHEMA_VERSION}). Atualiza a aplicação; a base de dados não foi alterada."
        )
    if version == SCHEMA_VERSION:
        return False
    c.execute("BEGIN IMMEDIATE")
    try:
        for target, step in MIGRATIONS:
            if version < target:
                step(c)
        c.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        c.commit()
        return True
    except Exception:
        c.rollback()
        raise


def _legacy_results(r):
    r = r or {}
    out = {}
    disks = r.get("smartDisks") or []
    if not disks and (r.get("smartDevice") or r.get("smartStatus") or r.get("smart") is not None):
        disks = [
            {
                "device": r.get("smartDevice", ""),
                "health": r.get("smartStatus", ""),
                "status": r.get("smart", ""),
            }
        ]
    if disks:
        out["smartDisks"] = disks
    ram = r.get("ramRows") or []
    if not ram and (r.get("ramMemory") or r.get("ramStatus") or r.get("ram") is not None):
        ram = [{"module": r.get("ramMemory", ""), "result": r.get("ramStatus", ""), "test": r.get("ram", "")}]
    if ram:
        out["ramRows"] = ram
    temps = r.get("tempRows") or []
    if not temps and any(
        r.get(k) not in (None, "") for k in ("tempIdle", "tempLoad", "tempGpu", "tempDisks")
    ):
        temps = [
            {"sensor": "CPU", "idle": r.get("tempIdle", ""), "load": r.get("tempLoad", ""), "value": ""},
            {"sensor": "GPU", "value": r.get("tempGpu", "")},
            {"sensor": "Discos", "value": r.get("tempDisks", "")},
        ]
    if temps:
        out["tempRows"] = temps
    tests = r.get("testRows") or []
    if not tests and (r.get("otherTests") or r.get("cpu")):
        tests = [
            {"name": "CPU", "result": r.get("cpu", "")},
            {"name": "Outros testes", "result": r.get("otherTests", "")},
        ]
    if tests:
        out["testRows"] = tests
    plan_ids = r.get("preventivePlanIds")
    if isinstance(plan_ids, list):
        out["preventivePlanIds"] = [str(x) for x in plan_ids if str(x).strip()]
    elif r.get("preventivePlanId"):
        # Legacy single-plan format: normalize it to the new list format.
        out["preventivePlanIds"] = [str(r["preventivePlanId"])]
    return out


def migrate_legacy_plans(c):
    """Move old per-equipment plans into one global plan catalogue once."""
    if c.execute("SELECT 1 FROM preventive_plans LIMIT 1").fetchone():
        return
    legacy = c.execute("SELECT * FROM maintenance_plans ORDER BY sort_order,id").fetchall()
    if not legacy:
        return
    by_sig = {}
    id_map = {}
    for r in legacy:
        sig = (r["name"].strip(), int(r["interval_months"] or 6), int(r["active"] or 0), r["notes"] or "")
        if sig in by_sig:
            pid = by_sig[sig]
        else:
            pid = r["id"]
            by_sig[sig] = pid
            c.execute(
                "INSERT INTO preventive_plans VALUES(?,?,?,?,?,?)",
                (
                    pid,
                    len(by_sig) - 1,
                    r["name"],
                    int(r["interval_months"] or 6),
                    int(r["active"] or 0),
                    r["notes"] or "",
                ),
            )
        id_map[r["id"]] = pid
        if r["equipment_id"]:
            c.execute("INSERT OR IGNORE INTO preventive_plan_equipment VALUES(?,?)", (pid, r["equipment_id"]))
    if id_map:
        for row in c.execute("SELECT id,results_json FROM maintenance").fetchall():
            try:
                r = json.loads(row["results_json"] or "{}")
            except Exception:
                r = {}
            old_ids = r.get("preventivePlanIds")
            if not isinstance(old_ids, list):
                old = r.get("preventivePlanId")
                old_ids = [old] if old else []
            new_ids = [id_map.get(str(pid), str(pid)) for pid in old_ids if str(pid).strip()]
            if new_ids:
                r["preventivePlanIds"] = new_ids
                r.pop("preventivePlanId", None)
                c.execute(
                    "UPDATE maintenance SET results_json=? WHERE id=?",
                    (json.dumps(r, ensure_ascii=False), row["id"]),
                )


def normalize_data(data):
    # Formato único: {equipment:[...], preventivePlans:[...]}.
    # P02: o Import JSON foi removido; o formato antigo em lista (V3/V5) deixou de ser aceite.
    if not isinstance(data, dict):
        raise ValueError("Os dados têm de ser um objeto JSON válido.")
    return {"equipment": data.get("equipment") or [], "preventivePlans": data.get("preventivePlans") or []}


ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def validate_id(value, label):
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise ValueError(f"ID de {label} inválido: {value}")
    return value


TEXT_MAX = 10_000
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EQUIPMENT_TEXT = (
    "name",
    "hostname",
    "role",
    "vendor",
    "model",
    "serial",
    "location",
    "status",
    "created",
    "notes",
    "cpu",
    "motherboard",
    "gpu",
    "nic",
    "psu",
    "bios",
)
COMPONENT_TEXT = ("name", "type", "model", "serial", "capacity", "health", "technology")
MAINTENANCE_TEXT = ("date", "type", "description", "areas", "notes")
PLAN_TEXT = ("name", "notes")
RESULT_ROWS = ("smartDisks", "ramRows", "tempRows", "testRows")


def _as_text(value, label):
    """P12: null passa a vazio, números passam a texto, o resto tem de ser texto."""
    if value is None:
        return ""
    if isinstance(value, bool) or isinstance(value, (dict, list)):
        raise ValueError(f"{label} tem de ser texto.")
    if isinstance(value, (int, float)):
        value = str(value)
    if not isinstance(value, str):
        raise ValueError(f"{label} tem de ser texto.")
    if len(value) > TEXT_MAX:
        raise ValueError(f"{label} excede {TEXT_MAX} caracteres.")
    return value


def _clean_fields(obj, fields, where):
    for key in fields:
        if key in obj:
            obj[key] = _as_text(obj[key], f"O campo «{key}» de {where}")


def _check_date(obj, key, where):
    value = obj.get(key) or ""
    if value:
        try:
            ok = bool(DATE_RE.match(value)) and date.fromisoformat(value).isoformat() == value
        except ValueError:
            ok = False
        if not ok:
            raise ValueError(f"Data inválida em {where}: «{value[:40]}». Usa o formato AAAA-MM-DD.")


def _normalize_fields(equipment, plans):
    """Normaliza tipos, limites e datas antes da validação de estrutura e referências (P12)."""
    for p in plans:
        if isinstance(p, dict):
            _clean_fields(p, PLAN_TEXT, f"preventiva «{p.get('name') or p.get('id')}»")
    for e in equipment:
        if not isinstance(e, dict):
            continue
        where = f"equipamento «{e.get('name') or e.get('id')}»"
        _clean_fields(e, EQUIPMENT_TEXT, where)
        _check_date(e, "created", where)
        for c in e.get("components") or []:
            if isinstance(c, dict):
                _clean_fields(c, COMPONENT_TEXT, f"componente «{c.get('name') or c.get('id')}» ({where})")
        for m in e.get("maintenance") or []:
            if not isinstance(m, dict):
                continue
            m_where = f"manutenção {m.get('id')} ({where})"
            _clean_fields(m, MAINTENANCE_TEXT, m_where)
            _check_date(m, "date", m_where)
            checks = m.get("checks")
            if isinstance(checks, dict):
                clean = {}
                for k, v in checks.items():
                    name = _as_text(k, f"Um item da checklist de {m_where}")
                    if len(name) > 200:
                        raise ValueError(f"Um item da checklist de {m_where} excede 200 caracteres.")
                    clean[name] = bool(v)
                m["checks"] = clean
            results = m.get("results")
            if isinstance(results, dict):
                for key in RESULT_ROWS:
                    for row in results.get(key) or []:
                        if isinstance(row, dict):
                            for k in list(row):
                                row[k] = _as_text(row[k], f"O campo «{k}» de {key} em {m_where}")


def _component_seen_in(component_index, cid, eid):
    """O componente, se está no equipamento eid ou já passou por ele (P22); senão None."""
    found = component_index.get(cid)
    if not found:
        return None
    current, comp = found
    previous = comp.get("previousEquipmentIds") or []
    return comp if current == eid or eid in [str(p) for p in previous] else None


def validate_data(data):
    data = normalize_data(data)
    equipment = data["equipment"]
    plans = data["preventivePlans"]
    if not isinstance(equipment, list) or not isinstance(plans, list):
        raise ValueError("Estrutura de dados inválida.")
    _normalize_fields(equipment, plans)

    equipment_ids = set()
    component_ids_global = set()
    maintenance_ids_global = set()
    # P22: um registo SMART/RAM pode referir um componente que entretanto foi transferido.
    # É válido se o componente estiver neste equipamento ou já tiver passado por ele.
    component_index = {}
    for e in equipment:
        if not isinstance(e, dict) or not isinstance(e.get("components") or [], list):
            continue
        eid_raw = str(e.get("id") or "")
        for ci, c in enumerate(e.get("components") or []):
            if isinstance(c, dict):
                component_index.setdefault(str(c.get("id") or f"c-{eid_raw}-{ci+1}"), (eid_raw, c))
    pseen = set()

    for i, p in enumerate(plans):
        if not isinstance(p, dict):
            raise ValueError(f"Manutenção preventiva #{i+1} inválida.")
        pid = validate_id(p.get("id") or f"p-{i+1}", "preventiva")
        if pid in pseen:
            raise ValueError(f"ID de preventiva duplicado: {pid}")
        pseen.add(pid)
        if not str(p.get("name", "")).strip():
            raise ValueError(f"A manutenção preventiva #{i+1} não tem nome.")
        raw_interval = p.get("intervalMonths")
        if isinstance(raw_interval, bool):
            raise ValueError(f'Intervalo inválido no plano {p.get("name","")}.')
        try:
            interval = 6 if raw_interval is None or raw_interval == "" else int(raw_interval)
        except (TypeError, ValueError):
            raise ValueError(f'Intervalo inválido no plano {p.get("name","")}.')
        if isinstance(raw_interval, float) and not raw_interval.is_integer():
            raise ValueError(f'Intervalo inválido no plano {p.get("name","")}.')
        if interval < 1 or interval > 120:
            raise ValueError("O intervalo preventivo deve estar entre 1 e 120 meses.")

    for i, e in enumerate(equipment):
        if not isinstance(e, dict):
            raise ValueError(f"Equipamento #{i+1} inválido.")
        eid = validate_id(e.get("id") or f"eq-{i+1}", "equipamento")
        if eid in equipment_ids:
            raise ValueError(f"ID de equipamento duplicado: {eid}")
        equipment_ids.add(eid)
        if not str(e.get("name", "")).strip():
            raise ValueError(f"O equipamento #{i+1} não tem nome.")

        for key in ("components", "maintenance", "preventivePlanIds"):
            if e.get(key) is not None and not isinstance(e.get(key), list):
                raise ValueError(f"{key} inválido no equipamento {eid}.")

        component_ids_local = set()
        components = e.get("components") or []
        for ci, c in enumerate(components):
            if not isinstance(c, dict):
                raise ValueError(f"Componente #{ci+1} inválido no equipamento {eid}.")
            cid = validate_id(c.get("id") or f"c-{eid}-{ci+1}", "componente")
            if cid in component_ids_local:
                raise ValueError(f"ID de componente duplicado no equipamento {eid}: {cid}")
            if cid in component_ids_global:
                raise ValueError(f"ID de componente duplicado: {cid}")
            component_ids_local.add(cid)
            component_ids_global.add(cid)
            c["type"] = normalize_component_type(c.get("type"))
            retired = c.get("retired", False)
            if retired is None:
                retired = False
            if not isinstance(retired, bool):
                raise ValueError(f"O campo «retired» do componente {cid} tem de ser verdadeiro ou falso.")
            c["retired"] = retired
            previous = c.get("previousEquipmentIds")
            if previous is None:
                previous = []
            if not isinstance(previous, list) or len(previous) > 100:
                raise ValueError(f"Histórico de equipamentos inválido no componente {cid}.")
            c["previousEquipmentIds"] = [validate_id(str(p), "equipamento anterior") for p in previous]
            if not str(c.get("name", "")).strip():
                raise ValueError(f"O componente #{ci+1} do equipamento {eid} não tem nome.")

        maintenance = e.get("maintenance") or []
        for mi, m in enumerate(maintenance):
            if not isinstance(m, dict):
                raise ValueError(f"Registo de manutenção #{mi+1} inválido no equipamento {eid}.")
            mid = validate_id(m.get("id") or f"m-{eid}-{mi+1}", "manutenção")
            if mid in maintenance_ids_global:
                raise ValueError(f"ID de manutenção duplicado: {mid}")
            maintenance_ids_global.add(mid)

            results = m.get("results")
            if results is None:
                results = {}
                m["results"] = results
            if not isinstance(results, dict):
                raise ValueError(f"Resultados inválidos na manutenção {mid} do equipamento {eid}.")
            preventive_plan_ids = results.get("preventivePlanIds")
            if preventive_plan_ids is None and results.get("preventivePlanId"):
                # Accept the previous single-plan representation while normalizing it.
                preventive_plan_ids = [results.get("preventivePlanId")]
                results["preventivePlanIds"] = [str(x) for x in preventive_plan_ids if str(x).strip()]
                results.pop("preventivePlanId", None)
            if preventive_plan_ids is None:
                preventive_plan_ids = []
                results["preventivePlanIds"] = []
            if not isinstance(preventive_plan_ids, list):
                raise ValueError(f"Preventivas inválidas na manutenção {mid} do equipamento {eid}.")
            seen_plan_ids = set()
            for preventive_plan_id in preventive_plan_ids:
                preventive_plan_id = str(preventive_plan_id).strip()
                if not preventive_plan_id:
                    continue
                if preventive_plan_id in seen_plan_ids:
                    raise ValueError(f"Preventiva duplicada na manutenção {mid}: {preventive_plan_id}")
                seen_plan_ids.add(preventive_plan_id)
                if preventive_plan_id not in pseen:
                    raise ValueError(
                        f"Preventiva inexistente referenciada pela manutenção {mid}: {preventive_plan_id}"
                    )

            checks = m.get("checks")
            if checks is None:
                checks = {}
                m["checks"] = checks
            if not isinstance(checks, dict):
                raise ValueError(f"Checklist inválida na manutenção {mid} do equipamento {eid}.")

            for result_key, label in (
                ("smartDisks", "SMART"),
                ("ramRows", "RAM"),
                ("tempRows", "temperaturas"),
                ("testRows", "testes"),
            ):
                rows = results.get(result_key)
                if rows is not None and not isinstance(rows, list):
                    raise ValueError(f"{result_key} inválido na manutenção {mid} do equipamento {eid}.")
                for ri, row in enumerate(rows or []):
                    if not isinstance(row, dict):
                        raise ValueError(
                            f"Registo de {label} #{ri+1} inválido na manutenção {mid} do equipamento {eid}."
                        )

            smart_disks = results.get("smartDisks") or []
            smart_device_ids = set()
            for di, d in enumerate(smart_disks):
                if not isinstance(d, dict):
                    raise ValueError(f"Registo SMART #{di+1} inválido na manutenção {mid}.")
                if not str(d.get("deviceId") or "").strip():
                    raise ValueError(f"O registo SMART #{di+1} da manutenção {mid} não tem deviceId.")
                device_id = validate_id(d.get("deviceId"), "deviceId SMART")
                if device_id in smart_device_ids:
                    raise ValueError(f"deviceId SMART duplicado na manutenção {mid}: {device_id}")
                smart_device_ids.add(device_id)
                comp = _component_seen_in(component_index, device_id, eid)
                if comp is None:
                    raise ValueError(
                        f"Disco SMART não encontrado nos componentes do equipamento {eid}: {device_id}"
                    )
                if comp.get("type") != "storage":
                    raise ValueError(f"O componente selecionado no SMART não é armazenamento: {device_id}")

            ram_rows = results.get("ramRows") or []
            ram_component_ids = set()
            for ri, row in enumerate(ram_rows):
                if not isinstance(row, dict):
                    raise ValueError(f"Registo RAM #{ri+1} inválido na manutenção {mid}.")
                if not str(row.get("ramComponentId") or "").strip():
                    raise ValueError(f"O registo RAM #{ri+1} da manutenção {mid} não tem ramComponentId.")
                ram_id = validate_id(row.get("ramComponentId"), "ramComponentId")
                if ram_id in ram_component_ids:
                    raise ValueError(f"ramComponentId duplicado na manutenção {mid}: {ram_id}")
                ram_component_ids.add(ram_id)
                comp = _component_seen_in(component_index, ram_id, eid)
                if comp is None:
                    raise ValueError(
                        f"Módulo RAM não encontrado nos componentes do equipamento {eid}: {ram_id}"
                    )
                if comp.get("type") != "ram":
                    raise ValueError(f"O componente selecionado no teste RAM não é memória RAM: {ram_id}")

        preventive_ids = e.get("preventivePlanIds") or []
        preventive_ids_seen = set()
        for pid in preventive_ids:
            pid = str(pid)
            if pid in preventive_ids_seen:
                raise ValueError(f"Preventiva duplicada no equipamento {eid}: {pid}")
            preventive_ids_seen.add(pid)
            if pid not in pseen:
                raise ValueError(f"Preventiva inexistente associada ao equipamento {eid}: {pid}")
    return data


def init(backup_before_migration=True):
    """Prepara a base de dados no arranque e após um restore.

    P17: se o esquema estiver desatualizado, guarda um snapshot em data/backups/ e migra
    numa transação. Uma base de uma versão mais recente impede o arranque.
    P07: o seed.json só é aplicado no primeiro arranque (sem a marca 'initialized')
    e apenas se a base de dados estiver vazia.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    c = db()
    try:
        c.execute("PRAGMA journal_mode=WAL")
        version = schema_version(c)
        if version < SCHEMA_VERSION:
            has_tables = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1").fetchone()
            if has_tables and backup_before_migration:
                name = _new_backup_name(f"pre-migration-v{version}-v{SCHEMA_VERSION}")
                create_full_snapshot_zip(backups_dir() / f"{name}.zip")
                log.info("Snapshot antes da migração: data/backups/%s.zip", name)
                _prune_backups()
            migrate(c)
            log.info("Base de dados migrada do esquema v%s para v%s.", version, SCHEMA_VERSION)
        elif version > SCHEMA_VERSION:
            migrate(c)  # lança SchemaTooNewError sem alterar nada
        initialized = c.execute("SELECT 1 FROM app_meta WHERE key='initialized'").fetchone()
        if not initialized:
            has_equipment = c.execute("SELECT 1 FROM equipment LIMIT 1").fetchone()
            has_plans = c.execute("SELECT 1 FROM preventive_plans LIMIT 1").fetchone()
            if not has_equipment and not has_plans:
                replace(c, seed())
        c.execute("INSERT OR REPLACE INTO app_meta(key,value) VALUES('initialized','1')")
        c.commit()
    finally:
        c.close()


def seed():
    with open(SEED_PATH, encoding="utf8") as f:
        x = json.load(f)
    return validate_data(x)


def replace(c, data):
    data = validate_data(data)
    equipment = data["equipment"]
    plans = data["preventivePlans"]
    for t in [
        "maintenance_checks",
        "maintenance",
        "preventive_plan_equipment",
        "preventive_plans",
        "maintenance_plans",
        "components",
        "equipment",
    ]:
        c.execute("DELETE FROM " + t)
    for i, p in enumerate(plans):
        pid = validate_id(p.get("id") or f"p-{i+1}", "preventiva")
        c.execute(
            "INSERT INTO preventive_plans VALUES(?,?,?,?,?,?)",
            (
                pid,
                i,
                p.get("name", ""),
                int(p.get("intervalMonths") or 6),
                1 if p.get("active", True) else 0,
                p.get("notes", ""),
            ),
        )
    for oi, e in enumerate(equipment):
        eid = str(e.get("id") or f"eq-{oi+1}")
        fields = [
            "id",
            "sort_order",
            "name",
            "hostname",
            "role",
            "vendor",
            "model",
            "serial",
            "location",
            "created",
            "status",
            "cpu",
            "motherboard",
            "gpu",
            "nic",
            "psu",
            "bios",
            "notes",
        ]
        vals = [eid, oi] + [e.get(k, "") for k in fields[2:]]
        c.execute(
            "INSERT INTO equipment(" + ",".join(fields) + ") VALUES(" + ",".join("?" * len(fields)) + ")",
            vals,
        )
        for i, x in enumerate(e.get("components") or []):
            c.execute(
                "INSERT INTO components(id,equipment_id,sort_order,name,type,model,serial,capacity,health,"
                "retired,previous_equipment_ids,technology) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(x.get("id") or f"c-{eid}-{i+1}"),
                    eid,
                    i,
                    x.get("name", ""),
                    normalize_component_type(x.get("type")),
                    x.get("model", ""),
                    x.get("serial", ""),
                    x.get("capacity", ""),
                    x.get("health", ""),
                    1 if x.get("retired") else 0,
                    json.dumps([str(p) for p in x.get("previousEquipmentIds") or []]),
                    x.get("technology", ""),
                ),
            )
        for pid in e.get("preventivePlanIds") or []:
            c.execute("INSERT OR IGNORE INTO preventive_plan_equipment VALUES(?,?)", (str(pid), eid))
        for i, m in enumerate(e.get("maintenance") or []):
            mid = str(m.get("id") or f"m-{eid}-{i+1}")
            r = _legacy_results(m.get("results") or {})
            c.execute(
                "INSERT INTO maintenance VALUES(?,?,?,?,?,?,?,?)",
                (
                    mid,
                    eid,
                    m.get("date", ""),
                    m.get("type", ""),
                    m.get("description", ""),
                    m.get("areas", ""),
                    m.get("notes", ""),
                    json.dumps(r, ensure_ascii=False),
                ),
            )
            for k, v in (m.get("checks") or {}).items():
                c.execute("INSERT INTO maintenance_checks VALUES(?,?,?)", (mid, str(k), 1 if v else 0))


class ConflictError(Exception):
    """A gravação partiu de uma revisão que já não é a atual (P04)."""

    def __init__(self, current):
        super().__init__("Os dados foram alterados entretanto.")
        self.current = current


def current_revision(c):
    row = c.execute("SELECT value FROM app_meta WHERE key='revision'").fetchone()
    return int(row[0]) if row else 0


def read_state():
    """Dados e revisão lidos na mesma transação, para serem coerentes entre si (P04)."""
    with state_access():
        c = db()
        try:
            c.execute("BEGIN")
            revision = current_revision(c)
            data = _read(c)
            c.commit()
            return data, revision
        finally:
            c.close()


def read():
    c = db()
    try:
        return _read(c)
    finally:
        c.close()


def _read(c):
    equipment_rows = c.execute("SELECT * FROM equipment ORDER BY sort_order,id").fetchall()
    out = []
    for e in equipment_rows:
        x = dict(e)
        x.pop("sort_order", None)
        x["components"] = []
        x["preventivePlanIds"] = []
        x["maintenance"] = []
        for r in c.execute(
            "SELECT id,name,type,model,serial,capacity,health,technology,retired,previous_equipment_ids "
            "FROM components WHERE equipment_id=? ORDER BY sort_order,id",
            (e["id"],),
        ):
            comp = dict(r)
            comp["retired"] = bool(comp["retired"])
            comp["previousEquipmentIds"] = json.loads(comp.pop("previous_equipment_ids") or "[]")
            x["components"].append(comp)
        for r in c.execute(
            "SELECT plan_id FROM preventive_plan_equipment WHERE equipment_id=? ORDER BY plan_id",
            (e["id"],),
        ):
            x["preventivePlanIds"].append(r["plan_id"])
        for m in c.execute(
            "SELECT * FROM maintenance WHERE equipment_id=? ORDER BY date DESC,id DESC", (e["id"],)
        ):
            z = dict(m)
            r = json.loads(z.pop("results_json") or "{}")
            checks = {
                q["check_name"]: bool(q["checked"])
                for q in c.execute(
                    "SELECT check_name,checked FROM maintenance_checks WHERE maintenance_id=?", (m["id"],)
                )
            }
            r = _legacy_results(r)
            x["maintenance"].append(
                {
                    "id": z["id"],
                    "date": z["date"],
                    "type": z["type"],
                    "description": z["description"],
                    "areas": z["areas"],
                    "notes": z["notes"],
                    "checks": checks,
                    "results": r,
                }
            )
        out.append(x)
    plans = [
        {
            "id": r["id"],
            "name": r["name"],
            "intervalMonths": r["interval_months"],
            "active": bool(r["active"]),
            "notes": r["notes"],
        }
        for r in c.execute(
            "SELECT id,name,interval_months,active,notes FROM preventive_plans ORDER BY sort_order,id"
        )
    ]
    return {"equipment": out, "preventivePlans": plans}


def prune_orphan_attachments(c):
    """Apaga as linhas de anexos cujo dono já não existe e devolve os ficheiros a apagar.

    P16 (B13): os ficheiros só são apagados depois do commit. Se a transação falhar,
    as linhas voltam e os ficheiros continuam lá.
    """
    valid = {("equipment", r["id"]) for r in c.execute("SELECT id FROM equipment")}
    valid |= {("component", r["id"]) for r in c.execute("SELECT id FROM components")}
    valid |= {("maintenance", r["id"]) for r in c.execute("SELECT id FROM maintenance")}
    rows = c.execute("SELECT id,owner_type,owner_id,stored_name FROM attachments").fetchall()
    files = []
    for r in rows:
        if (r["owner_type"], r["owner_id"]) not in valid:
            c.execute("DELETE FROM attachments WHERE id=?", (r["id"],))
            files.append(r["stored_name"])
    return files


def _remove_attachment_files(names):
    for name in names:
        path = attachment_path(name)
        if path is None:
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            log.warning("Não foi possível apagar o ficheiro de anexo %s: %s", name, e)


def write(data, base_revision):
    """Substitui o inventário, só se base_revision for a revisão atual (P04).

    BEGIN IMMEDIATE serializa as gravações: duas gravações feitas a partir da mesma
    revisão nunca passam as duas.
    """
    data = validate_data(data)
    with state_access():
        return _write(data, base_revision)


def _write(data, base_revision):
    c = db()
    try:
        c.execute("BEGIN IMMEDIATE")
        current = current_revision(c)
        if base_revision != current:
            raise ConflictError(current)
        replace(c, data)
        orphans = prune_orphan_attachments(c)
        ts = now()
        revision = current + 1
        c.execute("INSERT OR REPLACE INTO app_meta(key,value) VALUES('updated_at',?)", (ts,))
        c.execute("INSERT OR REPLACE INTO app_meta(key,value) VALUES('revision',?)", (str(revision),))
        c.commit()
        _remove_attachment_files(orphans)
        return ts, revision
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def _table_exists(c, name):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _snapshot_metadata(c):
    def count(table):
        return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] if _table_exists(c, table) else 0

    counts = {
        "equipment_count": count("equipment"),
        "component_count": count("components"),
        "maintenance_count": count("maintenance"),
        "preventive_plan_count": count("preventive_plans"),
    }
    attachments = []
    if _table_exists(c, "attachments"):
        attachments = [dict(r) for r in c.execute("SELECT * FROM attachments ORDER BY id").fetchall()]
    return counts, attachments


def _missing_entry(a):
    return {
        "id": a["id"],
        "owner_type": a.get("owner_type"),
        "owner_id": a.get("owner_id"),
        "original_name": a.get("original_name"),
        "display_name": a.get("display_name") or a.get("original_name"),
    }


def create_full_snapshot_zip(output_path):
    """Cria um snapshot consistente: cópia SQLite, ficheiros dos anexos e manifesto.

    P15: os anexos são escritos diretamente no ZIP, bloco a bloco, e o SHA-256 é calculado
    nessa mesma passagem; não há cópias intermédias nem ficheiros inteiros em memória.
    O ZIP é escrito como .part e só depois renomeado, por isso nunca fica um snapshot a meio.
    P16: um anexo em falta no disco não faz falhar o snapshot; fica em missing_attachments.
    Não inclui credenciais nem o código da aplicação.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    db_copy = new_tmp_path("snapshot-db", ".db")
    partial = output_path.with_name(output_path.name + ".part")
    try:
        src = db()
        try:
            dst = sqlite3.connect(db_copy)
            try:
                src.backup(dst)
                dst.commit()
            finally:
                dst.close()
        finally:
            src.close()
        copy = _connect(db_copy)
        try:
            counts, attachments = _snapshot_metadata(copy)
        finally:
            copy.close()

        present_sizes = 0
        for a in attachments:
            try:
                present_sizes += attachment_path(a["stored_name"]).stat().st_size
            except (OSError, AttributeError):
                pass
        ensure_free_space(db_copy.stat().st_size + present_sizes, output_path.parent)

        included, missing = [], []
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            z.write(db_copy, "database/homelab.db")
            for a in attachments:
                try:
                    path = attachment_path(a["stored_name"])
                    if path is None:
                        raise FileNotFoundError(a["stored_name"])
                    source = open(path, "rb")
                except FileNotFoundError:
                    missing.append(_missing_entry(a))
                    continue
                with source:
                    info = zipfile.ZipInfo(f"attachments/{a['id']}", date_time=time.localtime()[:6])
                    info.compress_type = zipfile.ZIP_STORED  # PDFs e imagens já vêm comprimidos
                    info.external_attr = 0o644 << 16
                    h = hashlib.sha256()
                    with z.open(info, "w") as out:
                        size = copy_stream(source, out, hasher=h)
                included.append(
                    {
                        "id": a["id"],
                        "owner_type": a["owner_type"],
                        "owner_id": a["owner_id"],
                        "original_name": a["original_name"],
                        "size": size,
                        "sha256": h.hexdigest(),
                    }
                )
            manifest = {
                "type": "homelab-register-snapshot",
                "format_version": 1,
                "created_at": now(),
                "application": "Homelab Register",
                "application_state": {
                    **counts,
                    "attachment_count": len(included),
                    "missing_attachment_count": len(missing),
                },
                "contents": {"database": "database/homelab.db", "attachments": "attachments/"},
                "attachments": included,
                "missing_attachments": missing,
            }
            z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        os.replace(partial, output_path)
        return manifest
    finally:
        db_copy.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)


def _safe_zip_member(name):
    p = Path(name)
    return name and not p.is_absolute() and ".." not in p.parts and "\\" not in name


def validate_snapshot_zip(zip_path):
    """Validate a snapshot completely without changing the live application."""
    zip_path = Path(zip_path)
    if not zip_path.is_file():
        raise ValueError("Ficheiro de snapshot não encontrado.")
    db_path = None
    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            # P15: sem testzip(); cada membro é lido uma vez, em blocos, e o CRC é
            # verificado automaticamente no fim dessa leitura.
            names = z.namelist()
            if len(names) != len(set(names)):
                raise ValueError("O snapshot contém entradas ZIP duplicadas.")
            if any(not _safe_zip_member(n) for n in names):
                raise ValueError("O snapshot contém caminhos inválidos.")
            required = {"manifest.json", "database/homelab.db"}
            if not required.issubset(set(names)):
                raise ValueError("Snapshot incompleto: faltam ficheiros obrigatórios.")
            if z.getinfo("manifest.json").file_size > MAX_MANIFEST_SIZE:
                raise ValueError("O manifesto do snapshot é demasiado grande.")
            manifest = json.loads(z.read("manifest.json").decode("utf-8"))
            if manifest.get("type") != "homelab-register-snapshot":
                raise ValueError("O ficheiro não é um snapshot do Homelab Register.")
            if int(manifest.get("format_version", 0)) != 1:
                raise ValueError("Versão de snapshot não suportada.")
            entries = manifest.get("attachments") or []
            missing_entries = manifest.get("missing_attachments") or []
            if not isinstance(entries, list) or not isinstance(missing_entries, list):
                raise ValueError("Manifesto inválido: listas de anexos mal formadas.")
            for a in entries + missing_entries:
                if not isinstance(a, dict):
                    raise ValueError("Manifesto inválido: entrada de anexo mal formada.")
                validate_id(str(a.get("id") or ""), "anexo")
            present_ids = [str(a.get("id")) for a in entries]
            missing_ids = [str(a.get("id")) for a in missing_entries]
            if len(set(present_ids + missing_ids)) != len(present_ids) + len(missing_ids):
                raise ValueError(
                    "Manifesto inválido: anexos repetidos ou declarados como presentes e em falta."
                )
            expected = {"manifest.json", "database/homelab.db"} | {
                f"attachments/{str(a.get('id'))}" for a in entries
            }
            if set(names) != expected:
                extra = sorted(set(names) - expected)
                missing = sorted(expected - set(names))
                detail = []
                if extra:
                    detail.append("ficheiros inesperados: " + ", ".join(extra[:5]))
                if missing:
                    detail.append("ficheiros em falta: " + ", ".join(missing[:5]))
                raise ValueError(
                    "Estrutura do snapshot inválida" + (": " + "; ".join(detail) if detail else ".")
                )

            # Tamanhos declarados verificados antes de ler (proteção contra ZIP-bombs).
            db_info = z.getinfo("database/homelab.db")
            if db_info.file_size > MAX_SNAPSHOT_DB_SIZE:
                raise ValueError("A base de dados do snapshot é demasiado grande.")
            for a in entries:
                declared = int(a.get("size", -1))
                info = z.getinfo(f"attachments/{a.get('id')}")
                if declared < 0 or declared > MAX_ATTACHMENT_SIZE or info.file_size != declared:
                    raise ValueError(f"Tamanho inválido no anexo {a.get('id')}.")
            ensure_free_space(db_info.file_size)
            db_path = new_tmp_path("validate-db", ".db")
            with z.open(db_info) as src, db_path.open("wb") as dst:
                copy_stream(src, dst, limit=db_info.file_size)
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("PRAGMA foreign_keys=ON")
                integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise ValueError("A base de dados do snapshot falhou o integrity_check.")
                fk = conn.execute("PRAGMA foreign_key_check").fetchall()
                if fk:
                    raise ValueError("A base de dados do snapshot contém referências inválidas.")
                db_attachment_rows = conn.execute(
                    "SELECT id,owner_type,owner_id,original_name,size,stored_name FROM attachments ORDER BY id"
                ).fetchall()
                db_attachment_ids = {r[0] for r in db_attachment_rows}
                # S11 (P24): o nome do ficheiro vem da BD do snapshot; tem de ser o próprio ID,
                # para nunca apontar para fora de attachments/.
                for aid, owner_type, _, _, _, stored_name in db_attachment_rows:
                    if (
                        not ID_RE.fullmatch(str(aid))
                        or stored_name != aid
                        or owner_type not in ATTACHMENT_OWNER_TYPES
                    ):
                        raise ValueError(f"O snapshot contém um anexo inválido: {str(aid)[:80]}")
                equipment_count = conn.execute("SELECT COUNT(*) FROM equipment").fetchone()[0]
                component_count = conn.execute("SELECT COUNT(*) FROM components").fetchone()[0]
                maintenance_count = conn.execute("SELECT COUNT(*) FROM maintenance").fetchone()[0]
                plan_count = conn.execute("SELECT COUNT(*) FROM preventive_plans").fetchone()[0]
            finally:
                conn.close()
            # P12 (S05): os dados do snapshot passam pelas mesmas regras de uma gravação,
            # para não entrar nada que a app depois não consiga gravar ou mostrar em segurança.
            snap = _connect(db_path)
            try:
                migrate(snap)
                validate_data(_read(snap))
            except ValueError as e:
                raise ValueError(f"O snapshot contém dados inválidos: {e}")
            finally:
                snap.close()
            manifest_ids = set(present_ids) | set(missing_ids)
            if db_attachment_ids != manifest_ids:
                raise ValueError("Os anexos do manifesto não correspondem à base de dados.")
            for a in entries:
                aid = str(a.get("id"))
                h = hashlib.sha256()
                with z.open(f"attachments/{aid}") as src:
                    size = copy_stream(src, _Discard(), limit=int(a["size"]), hasher=h)
                if size != int(a["size"]):
                    raise ValueError(f"Tamanho inválido no anexo {aid}.")
                if h.hexdigest() != str(a.get("sha256", "")):
                    raise ValueError(f"Hash inválido no anexo {aid}.")
            counts = manifest.get("application_state") or {}
            expected_counts = {
                "equipment_count": equipment_count,
                "component_count": component_count,
                "maintenance_count": maintenance_count,
                "preventive_plan_count": plan_count,
                "attachment_count": len(entries),
                "missing_attachment_count": len(missing_entries),
            }
            for key, actual in expected_counts.items():
                if key in counts and int(counts[key]) != actual:
                    raise ValueError(f"O manifesto não corresponde à base de dados ({key}).")
            return {
                "manifest": manifest,
                "equipment_count": equipment_count,
                "component_count": component_count,
                "maintenance_count": maintenance_count,
                "preventive_plan_count": plan_count,
                "attachment_count": len(entries),
                "missing_attachment_count": len(missing_entries),
                "missing_attachments": missing_entries,
                "created_at": manifest.get("created_at"),
                "size": zip_path.stat().st_size,
            }
    except zipfile.BadZipFile:
        raise ValueError("O ficheiro selecionado não é um ZIP válido ou está corrompido.")
    except json.JSONDecodeError:
        raise ValueError("O manifesto do snapshot é inválido.")
    # R04 (P27): erros da BD do snapshot passam a 400 com a razão, em vez de 500.
    except SchemaTooNewError:
        raise ValueError(
            "O snapshot foi criado por uma versão mais recente da aplicação. "
            "Atualiza a aplicação antes de o restaurar."
        )
    except sqlite3.DatabaseError:
        raise ValueError("A base de dados do snapshot está danificada ou não é do Homelab Register.")
    finally:
        if db_path is not None:
            db_path.unlink(missing_ok=True)


class _Discard:
    def write(self, chunk):
        return len(chunk)


BACKUPS_KEEP = AUTO_BACKUPS_KEEP


def backups_dir():
    """Pasta dos backups automáticos (P05). Nunca entra nos snapshots."""
    d = DATA_DIR / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _new_backup_name(kind="pre-restore"):
    stamp = datetime.now(timezone.utc)
    return f"{kind}-{stamp:%Y%m%d-%H%M%S}-{stamp.microsecond:06d}"


def _prune_backups():
    """Mantém só os BACKUPS_KEEP backups automáticos mais recentes de cada tipo."""
    for kind in ("pre-restore-", "pre-migration-"):
        entries = sorted(p for p in backups_dir().iterdir() if p.name.startswith(kind))
        for old in entries[:-BACKUPS_KEEP]:
            if old.is_dir():
                shutil.rmtree(old, ignore_errors=True)
            else:
                old.unlink(missing_ok=True)


RESTORE_JOURNAL = "restore-in-progress.json"


def recover_interrupted_restore():
    """No arranque: se um restore foi interrompido (p. ex. contentor morto pelo mem_limit),
    repõe o estado anterior. Só usa o que estiver comprovadamente na pasta de reserva."""
    journal = DATA_DIR / RESTORE_JOURNAL
    if not journal.exists():
        for leftover in DATA_DIR.glob(".restore-previous-*"):
            log.warning(
                "Pasta %s de um restore antigo: não foi apagada; verifica e remove à mão.", leftover.name
            )
        return False
    info = json.loads(journal.read_text(encoding="utf-8"))
    name = str(info.get("backup_dir", ""))
    backup_dir = DATA_DIR / name
    if name.startswith(".restore-previous-") and backup_dir.is_dir():
        old_db = backup_dir / "homelab.db"
        if old_db.exists():
            for p in (DB_PATH, Path(str(DB_PATH) + "-wal"), Path(str(DB_PATH) + "-shm")):
                p.unlink(missing_ok=True)
            shutil.move(str(old_db), str(DB_PATH))
            for suffix in ("-wal", "-shm"):
                side = backup_dir / ("homelab.db" + suffix)
                if side.exists():
                    shutil.move(str(side), str(DATA_DIR / side.name))
        old_att = backup_dir / "attachments"
        if old_att.exists():
            current_att = DATA_DIR / "attachments"
            if current_att.exists():
                shutil.rmtree(current_att)
            shutil.move(str(old_att), str(current_att))
        shutil.rmtree(backup_dir, ignore_errors=True)
    journal.unlink()
    log.warning("Restore interrompido encontrado no arranque: o estado anterior foi reposto.")
    return True


def restore_snapshot_zip(zip_path, summary=None):
    """Replace the current application state with a previously validated snapshot.

    The live database and attachment directory are moved aside first so a failure
    can be rolled back. Returns a summary of the restored snapshot.
    """
    zip_path = Path(zip_path)
    if summary is None:
        summary = validate_snapshot_zip(zip_path)
    # P05: guardar o estado atual antes de o substituir. Se o ZIP não puder ser
    # completo (faltam ficheiros de anexo no disco), guarda-se a cópia exata da
    # pasta em vez de bloquear o restore. Outros erros abortam o restore.
    safety_name = _new_backup_name()
    safety_zip = backups_dir() / f"{safety_name}.zip"
    # P16: o snapshot tolera anexos em falta, por isso o backup de segurança é sempre um ZIP
    # restaurável. Se não puder ser criado (p. ex. disco cheio), o restore não avança.
    create_full_snapshot_zip(safety_zip)
    with zipfile.ZipFile(zip_path, "r") as z:
        ensure_free_space(sum(i.file_size for i in z.infolist()))
    # B16 (P30): a troca dos ficheiros é exclusiva; espera pelos pedidos em curso e os
    # seguintes recebem RestoreInProgressError até terminar.
    with _state_lock:
        return _swap_in_snapshot(zip_path, summary, safety_zip)


def _swap_in_snapshot(zip_path, summary, safety_zip):
    previous = db()
    try:
        previous_revision = current_revision(previous)
    finally:
        previous.close()
    backup_dir = DATA_DIR / f".restore-previous-{uuid.uuid4().hex}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    journal = DATA_DIR / RESTORE_JOURNAL
    journal.write_text(json.dumps({"backup_dir": backup_dir.name, "started": now()}), encoding="utf-8")
    current_db = DB_PATH
    current_wal = Path(str(DB_PATH) + "-wal")
    current_shm = Path(str(DB_PATH) + "-shm")
    current_att = attachment_dir()
    old_att = backup_dir / "attachments"
    moved = False
    try:
        # Move the current state out of the way before replacing it.
        if current_db.exists():
            shutil.move(str(current_db), str(backup_dir / "homelab.db"))
        for sidecar in (current_wal, current_shm):
            if sidecar.exists():
                shutil.move(str(sidecar), str(backup_dir / sidecar.name))
        if current_att.exists():
            shutil.move(str(current_att), str(old_att))
        moved = True
        # P15: extração em blocos para ficheiros .part, renomeados só quando completos.
        with zipfile.ZipFile(zip_path, "r") as z:
            part = Path(str(current_db) + ".part")
            with z.open("database/homelab.db") as src, part.open("wb") as dst:
                copy_stream(src, dst)
            os.replace(part, current_db)
            current_att.mkdir(parents=True, exist_ok=True)
            for a in summary["manifest"].get("attachments") or []:
                aid = str(a["id"])
                part = current_att / (aid + ".part")
                with z.open(f"attachments/{aid}") as src, part.open("wb") as dst:
                    copy_stream(src, dst, limit=int(a["size"]))
                os.replace(part, current_att / aid)
        init(backup_before_migration=False)  # o próprio snapshot é o backup
        # One final validation against the now-live state.
        live = db()
        try:
            integrity = live.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise ValueError("A base de dados restaurada falhou o integrity_check.")
            count = live.execute("SELECT COUNT(*) FROM attachments").fetchone()[0]
            if count != summary["attachment_count"] + summary["missing_attachment_count"]:
                raise ValueError("O número de anexos restaurados não corresponde ao snapshot.")
            # P04: a revisão avança sempre, para que nenhuma página aberta antes do
            # restore consiga gravar por cima do estado restaurado.
            revision = max(previous_revision, current_revision(live)) + 1
            live.execute("INSERT OR REPLACE INTO app_meta(key,value) VALUES('revision',?)", (str(revision),))
            live.commit()
        finally:
            live.close()
        shutil.rmtree(backup_dir, ignore_errors=True)
        journal.unlink(missing_ok=True)
        summary["safety_backup"] = safety_zip.name
        _prune_backups()
        return summary
    except Exception:
        # Roll back to the previous state whenever replacement failed.
        try:
            if current_db.exists():
                current_db.unlink()
            if current_wal.exists():
                current_wal.unlink()
            if current_shm.exists():
                current_shm.unlink()
            if current_att.exists():
                shutil.rmtree(current_att, ignore_errors=True)
            if moved:
                old_db = backup_dir / "homelab.db"
                if old_db.exists():
                    shutil.move(str(old_db), str(current_db))
                for sidecar in (Path("-wal"), Path("-shm")):
                    src = backup_dir / ("homelab.db" + sidecar.name)
                    if src.exists():
                        shutil.move(str(src), str(DATA_DIR / src.name))
                if old_att.exists():
                    shutil.move(str(old_att), str(current_att))
        finally:
            shutil.rmtree(backup_dir, ignore_errors=True)
            journal.unlink(missing_ok=True)
            Path(str(current_db) + ".part").unlink(missing_ok=True)
        raise


def attachment_dir():
    d = DATA_DIR / "attachments"
    d.mkdir(parents=True, exist_ok=True)
    return d


def attachment_path(stored_name):
    """S11 (P24): caminho do ficheiro de um anexo, sempre dentro de attachments/.

    Devolve None se o nome guardado não for um ID válido (nunca sai da pasta).
    """
    name = str(stored_name or "")
    if not ID_RE.fullmatch(name):
        return None
    return attachment_dir() / name


def attachment_file_exists(stored_name):
    path = attachment_path(stored_name)
    return path is not None and path.is_file()


ATTACHMENT_OWNER_TYPES = {"equipment", "component", "maintenance"}


def attachment_owner_exists(c, owner_type, owner_id):
    tables = {"equipment": "equipment", "component": "components", "maintenance": "maintenance"}
    table = tables.get(owner_type)
    if not table:
        return False
    return bool(c.execute(f"SELECT 1 FROM {table} WHERE id=? LIMIT 1", (owner_id,)).fetchone())


ATTACHMENT_CATEGORIES = {"photo", "smart", "maintenance", "documentation", "diagnostic", "backup", "other"}


def list_attachments(owner_type=None, owner_id=None):
    with state_access():
        c = db()
        try:
            if owner_type and owner_id:
                rows = c.execute(
                    "SELECT id,owner_type,owner_id,original_name,display_name,category,mime_type,size,stored_name,created FROM attachments WHERE owner_type=? AND owner_id=? ORDER BY created DESC,id DESC",
                    (owner_type, owner_id),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT id,owner_type,owner_id,original_name,display_name,category,mime_type,size,stored_name,created FROM attachments ORDER BY created DESC,id DESC"
                ).fetchall()
            out = []
            for r in rows:
                item = dict(r)
                item["missing"] = not attachment_file_exists(item["stored_name"])
                if owner_type and owner_id:
                    item.pop("stored_name")
                out.append(item)
            return out
        finally:
            c.close()


def attachment_summary():
    with state_access():
        c = db()
        try:
            rows = c.execute(
                "SELECT owner_type,owner_id,COUNT(*) AS count FROM attachments GROUP BY owner_type,owner_id"
            ).fetchall()
            return {f"{r['owner_type']}:{r['owner_id']}": r["count"] for r in rows}
        finally:
            c.close()


def save_attachment_file(
    owner_type, owner_id, original_name, mime_type, source_path, display_name="", category="other"
):
    """P15: o anexo chega já gravado em data/tmp/ (em blocos) e é movido com uma renomeação atómica."""
    with state_access():
        c = db()
        aid = "att-" + uuid.uuid4().hex
        safe_name = (
            os.path.basename(str(original_name or "ficheiro")).replace("\x00", "").strip() or "ficheiro"
        )
        safe_name = safe_name[:255]
        display_name = str(display_name or "").strip()[:160] or safe_name
        category = category if category in ATTACHMENT_CATEGORIES else "other"
        target = attachment_dir() / aid
        size = Path(source_path).stat().st_size
        moved = False
        try:
            if not attachment_owner_exists(c, owner_type, owner_id):
                raise ValueError("O destino do anexo não existe.")
            os.replace(source_path, target)
            moved = True
            created = now()
            mime = mime_type or "application/octet-stream"
            c.execute(
                "INSERT INTO attachments(id,owner_type,owner_id,original_name,display_name,category,mime_type,size,stored_name,created) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (aid, owner_type, owner_id, safe_name, display_name, category, mime, size, aid, created),
            )
            c.commit()
            return {
                "id": aid,
                "owner_type": owner_type,
                "owner_id": owner_id,
                "original_name": safe_name,
                "display_name": display_name,
                "category": category,
                "mime_type": mime,
                "size": size,
                "created": created,
            }
        except Exception:
            c.rollback()
            if moved:
                target.unlink(missing_ok=True)
            raise
        finally:
            c.close()


def update_attachment(attachment_id, display_name=None, category=None):
    with state_access():
        c = db()
        try:
            r = c.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
            if not r:
                return None
            name = str(
                display_name if display_name is not None else (r["display_name"] or r["original_name"])
            ).strip()[:160]
            if not name:
                raise ValueError("O nome do anexo não pode ficar vazio.")
            cat = str(category if category is not None else (r["category"] or "other"))
            if cat not in ATTACHMENT_CATEGORIES:
                raise ValueError("Categoria de anexo inválida.")
            c.execute(
                "UPDATE attachments SET display_name=?,category=? WHERE id=?", (name, cat, attachment_id)
            )
            c.commit()
            return get_attachment(attachment_id)
        finally:
            c.close()


def get_attachment(attachment_id):
    with state_access():
        c = db()
        try:
            r = c.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
            return dict(r) if r else None
        finally:
            c.close()


def delete_attachment(attachment_id):
    with state_access():
        c = db()
        try:
            r = c.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
            if not r:
                return False
            c.execute("DELETE FROM attachments WHERE id=?", (attachment_id,))
            c.commit()
            _remove_attachment_files([r["stored_name"]])
            return True
        finally:
            c.close()
