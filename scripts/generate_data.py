#!/usr/bin/env python3
"""
Fetches data from SheetsDB and generates data.json for the Trustpilot dashboard.
Used by GitHub Actions (weekly) and can be run locally.

Usage:
  SHEETDB_URL=https://sheetdb.io/api/v1/XXXX python3 scripts/generate_data.py
"""

import json
import os
import sys
import requests
from datetime import datetime, timezone
from collections import defaultdict

MONTHS_PT = {1:'Jan',2:'Fev',3:'Mar',4:'Abr',5:'Mai',6:'Jun',
             7:'Jul',8:'Ago',9:'Set',10:'Out',11:'Nov',12:'Dez'}
MONTHS_ORDER = {v: k for k, v in MONTHS_PT.items()}

BUYER_MOTIVOS = {
    'Reembolso', 'Cobrança não reconhecida',
    'Conteúdo do produto', 'Compra não localizada'
}
CREATOR_MOTIVOS = {
    'Saques e comissões', 'Saques/Comissão', 'Saques/Com.',
    'Programa de afiliados', 'Suporte Produtor',
    'Cartão Hotmart', 'Atribuição de comissão',
    'Ferramentas', 'Página de vendas'
}
MOTIVO_MAP = {
    'Saques e comissões': 'Saques/Com.',
    'Saques/Comissão': 'Saques/Com.',
}


def parse_rating(val):
    if val is None:
        return None
    s = str(val).strip()
    stars = s.count('★')
    if stars > 0:
        return float(stars)
    try:
        return float(s.replace(',', '.'))
    except Exception:
        return None


def parse_month(val):
    if not val:
        return None
    s = str(val)[:10]
    for fmt in ('%Y-%m-%d', '%d/%m/%Y'):
        try:
            d = datetime.strptime(s, fmt)
            return f"{MONTHS_PT[d.month]}/{str(d.year)[2:]}"
        except Exception:
            continue
    return None


def categorize_bloqueio(status):
    s = str(status)
    has_tos = 'ToS' in s or 'Descumprimento' in s
    has_fraude = 'fraude' in s.lower()
    has_link = 'link de vendas' in s.lower()
    has_cb = 'chargeback' in s.lower()
    has_il = 'lícito' in s.lower()
    has_saque = 'Saque' in s
    if has_tos and has_cb and has_fraude:
        return 'ToS+Fraude+CB'
    if has_tos and has_cb:
        return 'ToS+Chargeback'
    if has_fraude and has_cb:
        return 'Fraude+CB'
    if has_fraude and has_link:
        return 'Fraude+Link'
    if has_tos:
        return 'ToS Grave'
    if has_fraude:
        return 'Fraude'
    if has_link:
        return 'Link Desativado'
    if has_cb:
        return 'Chargeback'
    if has_il:
        return 'Ilícitos'
    if has_saque:
        return 'Saque Bloqueado'
    return 'Outros'


def month_sort_key(label):
    parts = label.split('/')
    if len(parts) == 2:
        m, y = parts
        return int(y) * 100 + MONTHS_ORDER.get(m, 0)
    return 0


def process_rows(rows):
    total = negativas = uma_estrela = prod_rep = 0
    bloqueados = reavaliaram = subiram5 = positivas = 0
    regular_neg = buyer_neg = creator_neg = ambig_neg = 0
    uma_estrela_reav = 0

    monthly = defaultdict(lambda: defaultdict(int))
    motivos_neg = defaultdict(int)
    idiomas_neg = defaultdict(int)
    bloqueio_types = defaultdict(int)

    for row in rows:
        a = row.get('Data da avaliação', '')
        b = row.get('Idioma', '')
        d_raw = row.get('Classificação', '')
        e_raw = row.get('Houve reavaliação?') or row.get('Houve reavaliação', '')
        j_raw = row.get('Status Produto', '')
        l_raw = row.get('Status Produtor', '')
        m_raw = row.get('Motivo de contato', '')

        d_val = parse_rating(d_raw)
        if d_val not in [1, 2, 3, 4, 5]:
            continue

        total += 1
        rating = int(d_val)
        month_lbl = parse_month(a)
        if month_lbl:
            monthly[month_lbl][rating] += 1

        if d_val <= 2:
            negativas += 1
        if d_val == 1:
            uma_estrela += 1
        if d_val >= 4:
            positivas += 1

        if str(j_raw).strip() == 'Reprovado' and d_val <= 2:
            prod_rep += 1

        l_str = str(l_raw) if l_raw else ''
        if 'Bloqueio' in l_str and d_val <= 2:
            bloqueados += 1
            bloqueio_types[categorize_bloqueio(l_str)] += 1
        if l_str.strip() in ('Regular', '', 'None') and d_val <= 2:
            regular_neg += 1

        e_str = str(e_raw).strip() if e_raw else ''
        if e_str and e_str not in ('-', 'None', ''):
            reavaliaram += 1
            if parse_rating(e_str) == 5:
                subiram5 += 1
            if d_val == 1:
                uma_estrela_reav += 1

        if d_val <= 2:
            m_str = str(m_raw).strip() if m_raw else ''
            if m_str and m_str not in ('None', ''):
                m_norm = MOTIVO_MAP.get(m_str, m_str)
                motivos_neg[m_norm] += 1
                if m_str in BUYER_MOTIVOS:
                    buyer_neg += 1
                elif m_str in CREATOR_MOTIVOS:
                    creator_neg += 1
                else:
                    ambig_neg += 1
            else:
                ambig_neg += 1

            b_str = str(b).strip() if b else ''
            if b_str and b_str not in ('None', ''):
                idiomas_neg[b_str] += 1

    now = datetime.now()
    current_key = (now.year % 100) * 100 + now.month
    sorted_months = sorted(
        (m for m in monthly if month_sort_key(m) <= current_key),
        key=month_sort_key
    )
    monthly_data = {
        'labels': sorted_months,
        'datasets': {
            str(r): [monthly[m][r] for m in sorted_months]
            for r in [1, 2, 3, 4, 5]
        }
    }

    motivos_sorted = sorted(motivos_neg.items(), key=lambda x: -x[1])[:10]
    idiomas_sorted = sorted(idiomas_neg.items(), key=lambda x: -x[1])
    bloqueio_sorted = sorted(bloqueio_types.items(), key=lambda x: -x[1])

    top_idioma_pct = round(idiomas_sorted[0][1] / negativas * 100) if idiomas_sorted and negativas else 0
    top_idioma_name = idiomas_sorted[0][0] if idiomas_sorted else ''

    return {
        'lastUpdated': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'total': total,
        'negativas': negativas,
        'negativasPct': round(negativas / total * 100) if total else 0,
        'umaEstrela': uma_estrela,
        'umaEstrelaPct': round(uma_estrela / total * 100) if total else 0,
        'produtoReprovado': prod_rep,
        'bloqueados': bloqueados,
        'regularNeg': regular_neg,
        'reavaliaram': reavaliaram,
        'subiramCinco': subiram5,
        'positivas': positivas,
        'buyerNeg': buyer_neg,
        'creatorNeg': creator_neg,
        'ambigNeg': ambig_neg,
        'umaEstrelaReav': uma_estrela_reav,
        'topIdiomaPct': top_idioma_pct,
        'topIdiomaNome': top_idioma_name,
        'cobrancaCount': motivos_neg.get('Cobrança não reconhecida', 0),
        'monthlyData': monthly_data,
        'motivos': [{'label': k, 'value': v} for k, v in motivos_sorted],
        'idiomas': [{'label': k, 'value': v} for k, v in idiomas_sorted],
        'bloqueioTypes': [{'label': k, 'value': v} for k, v in bloqueio_sorted],
    }


def main():
    sheetdb_url = os.environ.get('SHEETDB_URL', '').strip()
    if not sheetdb_url:
        print('ERROR: SHEETDB_URL environment variable not set.', file=sys.stderr)
        sys.exit(1)

    sheet_name = os.environ.get('SHEET_NAME', 'Trustpilot')
    url = f"{sheetdb_url}?sheet={sheet_name}"

    print(f'Fetching data from SheetsDB (sheet: {sheet_name})...')
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    rows = resp.json()
    print(f'  → {len(rows)} rows fetched.')

    data = process_rows(rows)

    output_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data.json')
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f'data.json written ({data["total"]} avaliações, last updated: {data["lastUpdated"]})')


if __name__ == '__main__':
    main()
