"""Relatórios em PDF (P18): equipamento e manutenções.

Os cálculos de RAM e armazenamento (P23) e o estado das preventivas seguem exatamente
as mesmas regras da interface (app.js: ramSummary, storageSummary, planStatus); os testes
comparam os dois. A data de hoje e o fuso vêm do dispositivo que pede o relatório.
"""

import calendar
import math
import re

from .pdf import Document

STATUS = {"ok": "Operacional", "warn": "Atenção", "offline": "Offline"}
CATEGORIES = {
    "cpu": "CPU",
    "gpu": "GPU",
    "motherboard": "Motherboard",
    "ram": "RAM",
    "storage": "Armazenamento",
    "psu": "PSU",
    "network": "Rede",
    "cooler": "Cooler",
    "fan": "Ventoinha",
    "controller": "Controlador",
    "other": "Outro",
}
ATTACHMENT_CATEGORIES = {
    "photo": "Fotografia",
    "smart": "Relatório SMART",
    "maintenance": "Manutenção",
    "documentation": "Documentação",
    "diagnostic": "Diagnóstico",
    "backup": "Backup",
    "other": "Outro",
}
DIAG_LABELS = {
    "device": "Disco",
    "health": "Saúde",
    "wear": "Desgaste",
    "hours": "Horas ligado",
    "power_cycles": "Ciclos de energia",
    "data_read": "Dados lidos",
    "data_written": "Dados escritos",
    "unsafe_shutdowns": "Shutdowns inesperados",
    "errors": "Erros",
    "status": "Estado",
    "self_test": "Último teste SMART",
    "module": "Módulo",
    "capacity": "Capacidade",
    "test": "Teste",
    "result": "Resultado",
    "sensor": "Sensor",
    "idle": "Idle",
    "load": "Carga",
    "value": "Valor",
    "name": "Nome",
    "tool": "Ferramenta",
}
DIAG_BLOCKS = (
    (
        "SMART",
        "smartDisks",
        (
            "device",
            "health",
            "wear",
            "hours",
            "power_cycles",
            "data_read",
            "data_written",
            "unsafe_shutdowns",
            "errors",
            "self_test",
        ),
    ),
    ("RAM", "ramRows", ("module", "capacity", "test", "result", "errors")),
    ("Temperaturas", "tempRows", ("sensor", "idle", "load", "value")),
    ("Testes", "testRows", ("name", "tool", "result")),
)


def component_status(value):
    return STATUS.get(value) or value or "—"


# --- P23: RAM e armazenamento a partir dos componentes ativos -------------------------
_CAPACITY_RE = re.compile(r"^(\d+(?:[.,]\d+)?)\s*([tgm])i?b?$", re.I)


def parse_capacity_gb(text, base):
    m = _CAPACITY_RE.match(str(text or "").strip())
    if not m:
        return None
    n, unit = float(m.group(1).replace(",", ".")), m.group(2).lower()
    return n * base if unit == "t" else n / base if unit == "m" else n


def format_number(n):
    value = math.floor(n * 100 + 0.5) / 100  # como o Math.round do JS
    return (str(int(value)) if value == int(value) else repr(value)).replace(".", ",")


def format_capacity(gb, base):
    return f"{format_number(gb / base)} TB" if gb >= base else f"{format_number(gb)} GB"


def _active(e, kind):
    return [c for c in e.get("components") or [] if not c.get("retired") and c.get("type") == kind]


def _technologies(items):
    seen = []
    for c in items:
        t = str(c.get("technology") or "").strip()
        if t and t not in seen:
            seen.append(t)
    return "/".join(seen)


def _total(items, base):
    sizes = [parse_capacity_gb(c.get("capacity"), base) for c in items]
    known = [s for s in sizes if s is not None]
    return sizes, known, len(known) < len(sizes), sum(known)


def ram_summary(e):
    modules = _active(e, "ram")
    if not modules:
        return ""
    sizes, known, partial, total = _total(modules, 1024)
    head = f"{'≥ ' if partial else ''}{format_capacity(total, 1024)}" if known else "Capacidade desconhecida"
    if not partial and all(x == known[0] for x in known):
        breakdown = f"{len(modules)} × {format_capacity(known[0], 1024)}"
    else:
        parts = " + ".join("?" if x is None else format_number(x) for x in sizes)
        breakdown = f"{len(modules)} módulos: {parts} GB"
    tech = _technologies(modules)
    note = " — capacidade ilegível em algum módulo" if partial else ""
    return f"{head}{' ' + tech if tech else ''} ({breakdown}){note}"


def storage_summary(e):
    devices = _active(e, "storage")
    if not devices:
        return ""
    _, known, partial, total = _total(devices, 1000)
    head = f"{'≥ ' if partial else ''}{format_capacity(total, 1000)}" if known else "Capacidade desconhecida"
    tech = _technologies(devices)
    if len(devices) == 1:
        detail = f" {tech}" if tech else ""
    else:
        detail = " (" + " · ".join(x for x in (f"{len(devices)} dispositivos", tech) if x) + ")"
    note = " — capacidade ilegível em algum dispositivo" if partial else ""
    return f"{head}{detail}{note}"


# --- preventivas -----------------------------------------------------------------------
def add_months(date_text, months):
    """Soma meses a uma data AAAA-MM-DD, sem fusos (o dia fica no último do mês se não existir)."""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", str(date_text or ""))
    if not m:
        return ""
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    index = month - 1 + int(months)
    year, month = year + index // 12, index % 12 + 1
    day = min(day, calendar.monthrange(year, month)[1])
    return f"{year:04d}-{month:02d}-{day:02d}"


def plan_status(e, plan, today):
    dates = sorted(
        (
            m.get("date")
            for m in e.get("maintenance") or []
            if plan["id"] in ((m.get("results") or {}).get("preventivePlanIds") or []) and m.get("date")
        ),
        reverse=True,
    )
    last = dates[0] if dates else ""
    next_date = add_months(last, plan.get("intervalMonths")) if last else ""
    if not last:
        return "Nunca feita", "", ""
    return ("Atrasada" if next_date < today else "Agendada"), last, next_date


# --- blocos comuns ---------------------------------------------------------------------
def _size(n):
    n = int(n or 0)
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n} {unit}" if unit == "B" else f"{format_number(n)} {unit}"
        n /= 1024
    return f"{format_number(n)} GB"


def _plan_names(plans, m):
    by_id = {p["id"]: p["name"] for p in plans}
    return [by_id[pid] for pid in ((m.get("results") or {}).get("preventivePlanIds") or []) if pid in by_id]


def _component_names(data):
    return {c["id"]: c.get("name") for e in data["equipment"] for c in e.get("components") or []}


def _maintenance(doc, plans, records, component_names):
    for m in sorted(records, key=lambda x: x.get("date") or "", reverse=True):
        doc.subheading(f"{m.get('date') or 'Sem data'} — {m.get('type') or 'Manutenção'}")
        doc.paragraph(m.get("description"))
        if m.get("areas"):
            doc.paragraph(f"Áreas: {m['areas']}", gray=0.3)
        names = _plan_names(plans, m)
        if names:
            doc.paragraph(f"Preventivas realizadas: {' · '.join(names)}", gray=0.3)
        done = [k for k, v in (m.get("checks") or {}).items() if v]
        if done:
            doc.paragraph(f"Checklist feita: {' · '.join(done)}", gray=0.3)
        results = m.get("results") or {}
        for title, key, fields in DIAG_BLOCKS:
            rows = results.get(key) or []
            if not rows:
                continue
            doc.paragraph(title, size=9, bold=True)
            for row in rows:
                # O nome do disco/módulo vem do componente (pode ter sido transferido).
                cid = row.get("deviceId") or row.get("ramComponentId")
                label = str(component_names.get(cid) or row.get(fields[0]) or "").strip()
                values = [
                    f"{DIAG_LABELS.get(k, k)}: {row[k]}" for k in fields[1:] if str(row.get(k) or "").strip()
                ]
                doc.paragraph(" — ".join(x for x in (label, " · ".join(values)) if x), size=8.5, indent=10)
        if m.get("notes"):
            doc.paragraph(f"Notas: {m['notes']}", size=9, gray=0.3)
        doc.rule()


def _generated(generated):
    return f"Gerado em {generated}" if generated else ""


# --- relatórios ------------------------------------------------------------------------
def equipment_report(path, data, eid, attachments, today, generated):
    e = next(x for x in data["equipment"] if x["id"] == eid)
    plans = data.get("preventivePlans") or []
    names = {x["id"]: x["name"] for x in data["equipment"]}
    doc = Document(f"Relatório — {e.get('name')}", f"Homelab Register · {e.get('name')} · {today}")
    subtitle = " · ".join(
        x for x in (e.get("role"), f"{e.get('vendor') or ''} {e.get('model') or ''}".strip()) if x
    )
    doc.title_block(e.get("name") or "Equipamento", subtitle, _generated(generated))

    doc.heading("Identificação")
    doc.facts(
        [
            ("Hostname", e.get("hostname")),
            ("Função", e.get("role")),
            ("Fabricante", e.get("vendor")),
            ("Modelo", e.get("model")),
            ("Serial", e.get("serial")),
            ("Localização", e.get("location")),
            ("Entrada", e.get("created")),
            ("Estado", STATUS.get(e.get("status"), e.get("status") or "")),
        ]
    )
    if str(e.get("notes") or "").strip():
        doc.heading("Notas")
        doc.paragraph(e["notes"])

    doc.heading("Hardware")
    doc.facts(
        [
            ("CPU", e.get("cpu")),
            ("Motherboard", e.get("motherboard")),
            ("RAM", ram_summary(e)),
            ("GPU / iGPU", e.get("gpu")),
            ("Armazenamento", storage_summary(e)),
            ("Rede", e.get("nic")),
            ("PSU", e.get("psu")),
            ("BIOS", e.get("bios")),
        ]
    )

    components = e.get("components") or []
    doc.heading(f"Componentes ({sum(1 for c in components if not c.get('retired'))} ativos)")
    if components:
        ordered = [c for c in components if not c.get("retired")] + [
            c for c in components if c.get("retired")
        ]
        rows = []
        for c in ordered:
            previous = (c.get("previousEquipmentIds") or [])[-1:] or [None]
            moved = f"\nmovido de {names.get(previous[0], 'equipamento apagado')}" if previous[0] else ""
            status = " · ".join(
                x for x in ("Retirado" if c.get("retired") else "", component_status(c.get("health"))) if x
            )
            rows.append(
                [
                    f"{c.get('name')}{moved}",
                    CATEGORIES.get(c.get("type"), "Outro"),
                    "\n".join(
                        x for x in (c.get("model"), f"SN {c['serial']}" if c.get("serial") else "") if x
                    ),
                    " · ".join(x for x in (c.get("capacity"), c.get("technology")) if x),
                    status,
                ]
            )
        doc.table(
            ["Componente", "Categoria", "Modelo / Serial", "Capacidade", "Estado"],
            rows,
            [0.27, 0.15, 0.24, 0.17, 0.17],
        )
    else:
        doc.paragraph("Sem componentes registados.", gray=0.45)

    assigned = [p for p in plans if p["id"] in (e.get("preventivePlanIds") or [])]
    doc.heading("Manutenção preventiva")
    if assigned:
        rows = []
        for p in assigned:
            state, last, nxt = plan_status(e, p, today)
            n = p.get("intervalMonths")
            rows.append(
                [
                    p["name"] + ("" if p.get("active", True) else " (inativa)"),
                    f"{n} {'mês' if n == 1 else 'meses'}",
                    last,
                    nxt,
                    state,
                ]
            )
        doc.table(
            ["Preventiva", "Intervalo", "Última", "Próxima", "Estado"], rows, [0.34, 0.14, 0.17, 0.17, 0.18]
        )
    else:
        doc.paragraph("Nenhuma preventiva associada.", gray=0.45)

    records = e.get("maintenance") or []
    doc.heading(f"Histórico de manutenções ({len(records)})")
    if records:
        _maintenance(doc, plans, records, _component_names(data))
    else:
        doc.paragraph("Sem manutenções registadas.", gray=0.45)

    comp_names = {c["id"]: c.get("name") for c in components}
    maint = {m["id"]: m for m in records}
    owned = []
    for a in attachments:
        if a["owner_type"] == "equipment" and a["owner_id"] == eid:
            owner = "Equipamento"
        elif a["owner_type"] == "component" and a["owner_id"] in comp_names:
            owner = f"Componente: {comp_names[a['owner_id']]}"
        elif a["owner_type"] == "maintenance" and a["owner_id"] in maint:
            m = maint[a["owner_id"]]
            owner = f"Manutenção: {m.get('date') or 'sem data'}"
        else:
            continue
        owned.append(
            [
                owner,
                a.get("display_name") or a.get("original_name"),
                a.get("original_name"),
                ATTACHMENT_CATEGORIES.get(a.get("category"), "Outro"),
                "em falta" if a.get("missing") else _size(a.get("size")),
            ]
        )
    doc.heading(f"Anexos ({len(owned)})")
    if owned:
        doc.paragraph(
            "Os ficheiros não estão incluídos neste PDF; ficam guardados na aplicação.", size=8, gray=0.45
        )
        doc.spacer(6)
        doc.table(
            ["Associado a", "Nome", "Ficheiro", "Categoria", "Tamanho"], owned, [0.24, 0.24, 0.24, 0.15, 0.13]
        )
    else:
        doc.paragraph("Sem anexos.", gray=0.45)
    doc.save(path)


def maintenance_report(path, data, today, generated):
    plans = data.get("preventivePlans") or []
    equipment = [e for e in data["equipment"] if e.get("maintenance")]
    total = sum(len(e["maintenance"]) for e in equipment)
    doc = Document("Relatório de manutenções", f"Homelab Register · Manutenções · {today}")
    doc.title_block(
        "Relatório de manutenções",
        f"{total} intervenções em {len(equipment)} equipamentos",
        _generated(generated),
    )
    if not equipment:
        doc.paragraph("Sem manutenções registadas.", gray=0.45)
    for e in equipment:
        doc.heading(f"{e.get('name')} ({len(e['maintenance'])})")
        _maintenance(doc, plans, e["maintenance"], _component_names(data))
    doc.save(path)
