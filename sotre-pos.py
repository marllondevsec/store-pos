#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sistema simples de caixa (arquivo-texto, salvamento em tempo real)
Atualizações:
 - Gerenciamento de produtos (adicionar/editar/deletar/ajustar estoque/listar)
 - Integração do catálogo com o registro de vendas (sugestão de preço, atualização de estoque)
 - Agregação de produtos (quantidade + receita)
 - Relatórios semanais (todo sábado) e mensais (último dia do mês)
 - Painel de destaques (top vendidos)
 - Salva summaries em logs/
 - Ao primeiro uso: pede email do caixa e email da loja e salva em email_config.json
 - Armazena (opcional) senha do e-mail (codificada em base64)
 - Envia automaticamente o log ao fechar o caixa
 - Menu para configurar e-mails, senha, enviar manualmente e reenviar outbox
Compatível com Python 3.6+
"""
import os
import sys
import re
import json
import calendar
import base64
import shutil
import smtplib
from email.message import EmailMessage
from getpass import getpass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, date, timedelta
import uuid
import tempfile

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
SESSION_FILE = os.path.join(BASE_DIR, "current_session.txt")
EMAIL_CONFIG_FILE = os.path.join(BASE_DIR, "email_config.json")
OUTBOX_DIR = os.path.join(BASE_DIR, "outbox")
PRODUCTS_FILE = os.path.join(BASE_DIR, "products.json")
STORE_NAME = "PandaCell"

# formato de número: sempre 2 casas decimais
TWOPLACES = Decimal('0.01')

# ---------------------------
# utilitários de arquivo e dirs
# ---------------------------
def ensure_dirs():
    for d in (LOG_DIR, OUTBOX_DIR):
        if not os.path.exists(d):
            os.makedirs(d)

def atomic_write(path, content):
    dirn = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=dirn)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

def append_line(path, line):
    with open(path, 'a', encoding='utf-8') as f:
        f.write(line + "\n")
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass

# ---------------------------
# utilitários de terminal
# ---------------------------
def clear_screen():
    try:
        if os.name == 'nt':
            os.system('cls')
        else:
            os.system('clear')
    except Exception:
        # fallback: print algumas linhas novas
        print("\n" * 50)

def pause():
    try:
        input("\nPressione Enter para voltar ao menu...")
    except Exception:
        pass

# ---------------------------
# utilitários para Decimal e produtos
# ---------------------------
def parse_decimal(s, default=None):
    if s is None or (isinstance(s, str) and s.strip() == ""):
        return default
    try:
        return Decimal(str(s))
    except Exception:
        return default

def decimal_to_str(d):
    if d is None:
        return None
    return str(d.quantize(TWOPLACES, rounding=ROUND_HALF_UP))

# ---------------------------
# gerência de produtos (persistência em JSON)
# ---------------------------
def load_products():
    if not os.path.exists(PRODUCTS_FILE):
        return {}
    try:
        with open(PRODUCTS_FILE, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        # raw expected: dict lowername -> { 'name': ..., 'price': '19.90', 'stock': '10' or None }
        out = {}
        for k, v in raw.items():
            name = v.get('name') if isinstance(v, dict) else k
            price = parse_decimal(v.get('price')) if isinstance(v, dict) else None
            stock = parse_decimal(v.get('stock')) if isinstance(v, dict) else None
            out[k] = {'name': name, 'price': price, 'stock': stock}
        return out
    except Exception:
        return {}

def save_products(products):
    try:
        raw = {}
        for k, v in products.items():
            raw[k] = {
                'name': v.get('name'),
                'price': decimal_to_str(v.get('price')) if v.get('price') is not None else None,
                'stock': decimal_to_str(v.get('stock')) if v.get('stock') is not None else None
            }
        atomic_write(PRODUCTS_FILE, json.dumps(raw, indent=2, ensure_ascii=False))
        return True
    except Exception as e:
        print("Erro salvando produtos:", e)
        return False

def find_product_by_name(products, name):
    if not name:
        return None, None
    key = name.strip().lower()
    # direct match
    if key in products:
        return key, products[key]
    # try partial match: find first that contains the name
    for k, v in products.items():
        if name.strip().lower() in k:
            return k, v
    return None, None

def list_products(products):
    if not products:
        print("Nenhum produto cadastrado.")
        return
    items = sorted(products.items(), key=lambda x: x[1].get('name', x[0]).lower())
    print("\n=== PRODUTOS CADASTRADOS ===")
    print("IDX | Nome | Preço | Estoque")
    for i, (k, v) in enumerate(items, 1):
        price = money(v['price']) if v.get('price') is not None else "-"
        stock = money(v['stock']) if v.get('stock') is not None else "-"
        print(f"{i:>3} | {v.get('name')} | R$ {price} | {stock}")
    return items

def add_product_interactive():
    products = load_products()
    name = input("Nome do produto: ").strip()
    if not name:
        print("Nome vazio. Cancelando.")
        return
    key = name.lower()
    if key in products:
        print("Produto já existe no catálogo. Use editar para alterar.")
        return
    price_raw = input("Preço unitário (ex 19.90) [opcional]: ").strip()
    price = parse_decimal(price_raw)
    stock_raw = input("Estoque inicial (quantidade) [opcional]: ").strip()
    stock = parse_decimal(stock_raw)
    products[key] = {'name': name, 'price': price, 'stock': stock}
    if save_products(products):
        print("Produto adicionado.")
    else:
        print("Falha ao salvar produto.")

def select_product_by_index(products, prompt_text="Escolha o produto (idx): "):
    items = list_products(products)
    if not items:
        return None, None
    sel = input(prompt_text).strip()
    try:
        idx = int(sel)
        if 1 <= idx <= len(items):
            k, v = items[idx-1]
            return k, v
    except Exception:
        # try by name
        k, v = find_product_by_name(products, sel)
        return k, v
    print("Seleção inválida.")
    return None, None

def edit_product_interactive():
    products = load_products()
    k, v = select_product_by_index(products, prompt_text="Escolha produto por índice ou digite nome: ")
    if not k:
        return
    print(f"\nEditando produto: {v['name']}")
    new_name = input(f"Novo nome [{v['name']}]: ").strip()
    if new_name:
        new_key = new_name.lower()
    else:
        new_name = v['name']
        new_key = k
    price_old = money(v['price']) if v.get('price') is not None else ""
    price_raw = input(f"Novo preço (ex 19.90) [{price_old}]: ").strip()
    price = parse_decimal(price_raw, default=v.get('price')) if price_raw != "" else v.get('price')
    stock_old = money(v['stock']) if v.get('stock') is not None else ""
    stock_raw = input(f"Novo estoque [{stock_old}] (deixe em branco para manter): ").strip()
    stock = parse_decimal(stock_raw, default=v.get('stock')) if stock_raw != "" else v.get('stock')
    # remove old key if name changed
    if new_key != k:
        del products[k]
    products[new_key] = {'name': new_name, 'price': price, 'stock': stock}
    if save_products(products):
        print("Produto atualizado.")
    else:
        print("Falha ao salvar alterações.")

def delete_product_interactive():
    products = load_products()
    k, v = select_product_by_index(products, prompt_text="Escolha produto para deletar (idx) ou digite nome: ")
    if not k:
        return
    confirm = input(f"Confirma remoção do produto '{v['name']}'? (s/N): ").strip().lower()
    if confirm != 's':
        print("Operação cancelada.")
        return
    try:
        del products[k]
        save_products(products)
        print("Produto removido.")
    except Exception as e:
        print("Falha ao remover produto:", e)

def adjust_product_quantity_interactive():
    products = load_products()
    k, v = select_product_by_index(products, prompt_text="Escolha produto (idx) ou digite nome: ")
    if not k:
        return
    cur = v.get('stock')
    cur_disp = money(cur) if cur is not None else "(sem estoque definido)"
    print(f"Produto: {v['name']} | Estoque atual: {cur_disp}")
    op = input("Operação: 1) adicionar  2) remover  3) definir valor  (1/2/3): ").strip()
    if op not in ('1','2','3'):
        print("Operação inválida.")
        return
    val_raw = input("Quantidade: ").strip()
    val = parse_decimal(val_raw)
    if val is None:
        print("Quantidade inválida.")
        return
    if op == '1':
        new = (cur or Decimal('0')) + val
    elif op == '2':
        new = (cur or Decimal('0')) - val
        if new < 0:
            print("Aviso: estoque ficou negativo.")
    else:
        new = val
    products[k]['stock'] = new
    if save_products(products):
        print(f"Estoque atualizado: {money(new)}")
    else:
        print("Falha ao salvar estoque.")

def manage_products_menu():
    while True:
        clear_screen()
        products = load_products()
        print("\n=== GERENCIAR PRODUTOS ===")
        print("1) Listar produtos")
        print("2) Adicionar produto")
        print("3) Editar produto")
        print("4) Deletar produto")
        print("5) Ajustar estoque (adicionar/remover/definir)")
        print("0) Voltar")
        choice = input("Escolha: ").strip()
        clear_screen()
        if choice == '1':
            list_products(products)
            pause()
        elif choice == '2':
            add_product_interactive()
            pause()
        elif choice == '3':
            edit_product_interactive()
            pause()
        elif choice == '4':
            delete_product_interactive()
            pause()
        elif choice == '5':
            adjust_product_quantity_interactive()
            pause()
        elif choice == '0':
            break
        else:
            print("Opção inválida.")
            pause()

# ---------------------------
# dinheiro e logs
# ---------------------------
def money(value):
    if value is None:
        return "0.00"
    return f"{value.quantize(TWOPLACES, rounding=ROUND_HALF_UP)}"

def get_log_path(session_date):
    filename = f"{STORE_NAME}_{session_date}.txt"
    return os.path.join(LOG_DIR, filename)

def start_session():
    today = date.today().isoformat()
    info = load_session()
    if info and info.get('state') == 'OPEN' and info.get('date') == today:
        print(f"Existe uma sessão aberta para {today}. Retomando sessão.")
        return today
    save_session('OPEN', today)
    logp = get_log_path(today)
    if not os.path.exists(logp):
        header = (
            f"# Log de vendas - {STORE_NAME}\n"
            f"# Data: {today}\n"
            f"# Formato: timestamp | id | produto | qtd | unit_price | subtotal\n"
        )
        atomic_write(logp, header)
    print(f"Caixa aberto para {today}.")
    return today

def add_sale(session_date):
    logp = get_log_path(session_date)
    products = load_products()
    produto = input("Nome do produto: ").strip()
    if produto == "":
        print("Produto vazio. Operação cancelada.")
        return
    # if product exists in catalog, suggest price and show stock
    key, prod = find_product_by_name(products, produto)
    suggested_price = None
    suggested_stock = None
    if prod:
        suggested_price = prod.get('price')
        suggested_stock = prod.get('stock')
        print(f"Produto encontrado no catálogo: {prod['name']}")
        if suggested_price is not None:
            print(f"Preço sugerido: R$ {money(suggested_price)} (Enter para aceitar)")
        if suggested_stock is not None:
            print(f"Estoque atual: {money(suggested_stock)}")
    qtd_raw = input("Quantidade (default 1): ").strip()
    if qtd_raw == "":
        qtd = Decimal(1)
    else:
        try:
            qtd = Decimal(qtd_raw)
        except InvalidOperation:
            print("Quantidade inválida. Use número. Operação cancelada.")
            return
    if suggested_price is not None:
        price_raw = input(f"Preço unitário [{money(suggested_price)}]: ").strip()
        if price_raw == "":
            price = suggested_price
        else:
            try:
                price = Decimal(price_raw)
            except Exception:
                print("Preço inválido. Operação cancelada.")
                return
    else:
        price_raw = input("Preço unitário (ex: 19.90): ").strip()
        try:
            price = Decimal(price_raw)
        except Exception:
            print("Preço inválido. Operação cancelada.")
            return
    subtotal = (qtd * price).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    ts = datetime.now().isoformat(sep=' ', timespec='seconds')
    txid = uuid.uuid4().hex[:8]
    line = f"{ts} | {txid} | {produto} | {money(qtd)} | {money(price)} | {money(subtotal)}"
    try:
        append_line(logp, line)
    except Exception as e:
        print("Erro salvando venda:", e)
        return
    print(f"Venda registrada: {produto} x{money(qtd)}  subtotal: R$ {money(subtotal)}")
    # atualizar estoque se produto no catálogo
    if prod and prod.get('stock') is not None:
        update = input("Atualizar estoque do catálogo subtraindo esta quantidade? (s/N): ").strip().lower()
        if update == 's':
            new_stock = prod.get('stock') - qtd
            products[key]['stock'] = new_stock
            save_products(products)
            print(f"Estoque atualizado: {money(new_stock)}")

def list_sales(session_date, show_lines=50):
    logp = get_log_path(session_date)
    if not os.path.exists(logp):
        print("Nenhuma venda registrada para esta data.")
        return
    print(f"Últimas vendas ({logp}):\n")
    with open(logp, 'r', encoding='utf-8') as f:
        lines = [l.rstrip("\n") for l in f.readlines() if l.strip() and not l.startswith("#")]
    if not lines:
        print("Sem registros ainda.")
        return
    for l in lines[-show_lines:]:
        print(l)

def compute_total(session_date):
    logp = get_log_path(session_date)
    total = Decimal('0.00')
    if not os.path.exists(logp):
        return total
    with open(logp, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 6:
                try:
                    subtotal = Decimal(parts[5])
                    total += subtotal
                except Exception:
                    continue
    return total.quantize(TWOPLACES, rounding=ROUND_HALF_UP)

# ---------------------------
# sessão
# ---------------------------
def session_exists():
    return os.path.exists(SESSION_FILE)

def load_session():
    if not session_exists():
        return None
    try:
        with open(SESSION_FILE, 'r', encoding='utf-8') as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
        info = {}
        for line in lines:
            if '=' in line:
                k, v = line.split('=', 1)
                info[k.strip()] = v.strip()
        if 'state' in info and 'date' in info:
            return info
    except Exception:
        pass
    return None

def save_session(state, session_date):
    content = f"state={state}\ndate={session_date}\nstore={STORE_NAME}\n"
    atomic_write(SESSION_FILE, content)

def close_cash(session_date, email_conf):
    info = load_session()
    if not info or info.get('state') != 'OPEN' or info.get('date') != session_date:
        print("Nenhuma sessão aberta correspondente para fechar.")
        return
    total = compute_total(session_date)
    print("\n=== FECHAMENTO DO CAIXA ===")
    print(f"Loja: {STORE_NAME}")
    print(f"Data: {session_date}")
    print(f"Total do dia: R$ {money(total)}")
    confirm = input("Confirmar fechamento do caixa? (s/N): ").strip().lower()
    if confirm != 's':
        print("Fechamento cancelado.")
        return
    logp = get_log_path(session_date)
    summary_line = f"# FECHAMENTO: {datetime.now().isoformat(sep=' ', timespec='seconds')} | TOTAL R$ {money(total)}"
    append_line(logp, summary_line)
    save_session('CLOSED', session_date)
    print("Caixa fechado. Registro salvo.")
    # enviar automaticamente o log do dia
    send_log_with_handling(email_conf, logp, session_date, total)

# ---------------------------
# agregação / relatórios
# ---------------------------
LOG_DATE_RE = re.compile(rf"^{re.escape(STORE_NAME)}_(\d{{4}}-\d{{2}}-\d{{2}})\.txt$")

def list_log_files():
    files = []
    if not os.path.exists(LOG_DIR):
        return files
    for fname in os.listdir(LOG_DIR):
        m = LOG_DATE_RE.match(fname)
        if m:
            try:
                dt = datetime.strptime(m.group(1), "%Y-%m-%d").date()
                files.append((dt, os.path.join(LOG_DIR, fname)))
            except Exception:
                continue
    files.sort()
    return files

def parse_log_file(path):
    entries = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 6:
                continue
            try:
                ts = datetime.strptime(parts[0], "%Y-%m-%d %H:%M:%S")
            except Exception:
                try:
                    ts = datetime.fromisoformat(parts[0])
                except Exception:
                    ts = None
            product = parts[2]
            try:
                qty = Decimal(parts[3])
            except Exception:
                qty = Decimal('0')
            try:
                price = Decimal(parts[4])
            except Exception:
                price = Decimal('0.00')
            try:
                subtotal = Decimal(parts[5])
            except Exception:
                subtotal = (qty * price).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
            entries.append({
                'ts': ts,
                'product': product,
                'qty': qty,
                'unit_price': price,
                'subtotal': subtotal
            })
    return entries

def aggregate_products_between(start_date, end_date):
    agg = {}
    for dt, path in list_log_files():
        if dt < start_date or dt > end_date:
            continue
        for e in parse_log_file(path):
            key = e['product'].strip()
            key_norm = key.lower()
            if key_norm not in agg:
                agg[key_norm] = {'product': key, 'qty': Decimal('0'), 'revenue': Decimal('0.00')}
            try:
                agg[key_norm]['qty'] += e['qty']
                agg[key_norm]['revenue'] += e['subtotal']
            except Exception:
                continue
    for v in agg.values():
        v['qty'] = v['qty'].quantize(TWOPLACES, rounding=ROUND_HALF_UP)
        v['revenue'] = v['revenue'].quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    return agg

def sorted_agg_list(agg, by='qty', top_n=20):
    items = list(agg.values())
    reverse = True
    if by == 'qty':
        items.sort(key=lambda x: (x['qty'], x['revenue']), reverse=reverse)
    else:
        items.sort(key=lambda x: (x['revenue'], x['qty']), reverse=reverse)
    return items[:top_n]

def save_summary(text, fname):
    path = os.path.join(LOG_DIR, fname)
    atomic_write(path, text)
    return path

def format_agg_text(agg_items, title, period_desc):
    lines = []
    lines.append(f"# {title}")
    lines.append(f"# Período: {period_desc}")
    lines.append(f"# Gerado em: {datetime.now().isoformat(sep=' ', timespec='seconds')}")
    lines.append("# Formato: rank | produto | quant_total | receita_total")
    lines.append("")
    if not agg_items:
        lines.append("Sem vendas neste período.")
    else:
        for i, it in enumerate(agg_items, 1):
            lines.append(f"{i} | {it['product']} | {money(it['qty'])} | R$ {money(it['revenue'])}")
    return "\n".join(lines)

def show_top_week():
    today = date.today()
    start = today - timedelta(days=6)
    end = today
    agg = aggregate_products_between(start, end)
    items = sorted_agg_list(agg, by='qty', top_n=50)
    period_desc = f"{start.isoformat()} até {end.isoformat()}"
    text = format_agg_text(items, "Top vendidos - Semana", period_desc)
    fname = f"{STORE_NAME}_summary_week_{start.isoformat()}_to_{end.isoformat()}.txt"
    saved = save_summary(text, fname)
    print("\n=== TOP VENDIDOS (SEMANA) ===")
    print(text)
    print(f"\nResumo salvo em: {saved}")
    return items

def show_top_month():
    today = date.today()
    start = today.replace(day=1)
    end = today
    agg = aggregate_products_between(start, end)
    items = sorted_agg_list(agg, by='qty', top_n=50)
    period_desc = f"{start.isoformat()} até {end.isoformat()}"
    text = format_agg_text(items, "Top vendidos - Mês", period_desc)
    fname = f"{STORE_NAME}_summary_month_{today.strftime('%Y-%m')}.txt"
    saved = save_summary(text, fname)
    print("\n=== TOP VENDIDOS (MÊS) ===")
    print(text)
    print(f"\nResumo salvo em: {saved}")
    return items

def show_panel(top_n=5):
    print("\n=== PAINEL DE DESTAQUES ===")
    week = show_top_week()
    month = show_top_month()
    print("\n--- Top semana (resumido) ---")
    for i, it in enumerate(week[:top_n], 1):
        print(f"{i}. {it['product']} — qty: {money(it['qty'])} — receita: R$ {money(it['revenue'])}")
    print("\n--- Top mês (resumido) ---")
    for i, it in enumerate(month[:top_n], 1):
        print(f"{i}. {it['product']} — qty: {money(it['qty'])} — receita: R$ {money(it['revenue'])}")
    print("\n(Os summaries completos também foram salvos em logs/)")

def is_last_day_of_month(d):
    nxt = d + timedelta(days=1)
    return nxt.month != d.month

def auto_show_periodic_reports():
    today = date.today()
    if today.weekday() == 5:
        print("\nHoje é sábado — gerando resumo semanal automaticamente.")
        show_top_week()
    if is_last_day_of_month(today):
        print("\nHoje é o último dia do mês — gerando resumo mensal automaticamente.")
        show_top_month()

# ---------------------------
# configuração e armazenamento de e-mail
# ---------------------------
def is_valid_email(addr):
    if not addr or '@' not in addr:
        return False
    name, domain = addr.rsplit('@', 1)
    if not name or '.' not in domain:
        return False
    if ' ' in addr:
        return False
    return True

def load_email_config():
    if not os.path.exists(EMAIL_CONFIG_FILE):
        return {}
    try:
        with open(EMAIL_CONFIG_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return {}
            return data
    except Exception:
        return {}

def save_email_config(conf):
    try:
        atomic_write(EMAIL_CONFIG_FILE, json.dumps(conf, indent=2, ensure_ascii=False))
        return True
    except Exception as e:
        print("Erro salvando configuração de e-mail:", e)
        return False

def prompt_email_setup(existing_conf=None, ask_password=False):
    """
    Nova ordem: pedir primeiro e-mail do caixa e senha (se ask_password=True),
    depois perguntar e-mail destino.
    """
    print("\n=== Configuração de E-mails ===")
    if existing_conf is None:
        existing_conf = {}
    default_from = existing_conf.get('email_from', '')
    default_to = existing_conf.get('email_to', '')
    # email_from
    while True:
        prompt = "E-mail do caixa (emissor) [{}]: ".format(default_from) if default_from else "E-mail do caixa (emissor): "
        em_from = input(prompt).strip()
        if em_from == "" and default_from:
            em_from = default_from
        if is_valid_email(em_from):
            break
        print("E-mail inválido. Tente novamente (ex: pandacell.caixa@gmail.com).")
    conf = existing_conf.copy()
    conf['email_from'] = em_from

    # Se solicitado, pedir a senha do caixa agora (explicando que deve ser senha de app)
    if ask_password:
        print("\nInforme a senha do e-mail do caixa. Se estiver usando Gmail, informe a *app password* (senha de app), não a senha normal da conta.")
        pwd = getpass("Senha (app password) do e-mail do caixa (deixe em branco para não salvar): ")
        if pwd:
            conf['email_password_b64'] = base64.b64encode(pwd.encode('utf-8')).decode('ascii')

    # email_to (destinatário)
    while True:
        prompt = "E-mail da loja (destinatário) [{}]: ".format(default_to) if default_to else "E-mail da loja (destinatário): "
        em_to = input(prompt).strip()
        if em_to == "" and default_to:
            em_to = default_to
        if is_valid_email(em_to):
            break
        print("E-mail inválido. Tente novamente (ex: pandacell@gmail.com).")

    conf['email_to'] = em_to

    if save_email_config(conf):
        print("Configuração de e-mails salva em:", EMAIL_CONFIG_FILE)
    else:
        print("Falha ao salvar configuração de e-mails.")
    return conf

def configure_emails_interactive():
    conf = load_email_config()
    return prompt_email_setup(existing_conf=conf, ask_password=False)

def configure_password_interactive():
    conf = load_email_config()
    print("\n=== Configurar / Alterar senha do e-mail do caixa ===")
    print("Observação: use 'app password' (senha de app) se seu provedor exigir, ex: Gmail.")
    pwd = getpass("Informe a senha (app password). Deixe em branco para remover a senha salva: ")
    if pwd:
        conf['email_password_b64'] = base64.b64encode(pwd.encode('utf-8')).decode('ascii')
        saved = save_email_config(conf)
        if saved:
            print("Senha salva (codificada) em email_config.json.")
        else:
            print("Falha ao salvar senha.")
    else:
        if 'email_password_b64' in conf:
            del conf['email_password_b64']
            save_email_config(conf)
            print("Senha removida do arquivo de configuração.")
        else:
            print("Nenhuma senha estava salva.")
    return conf

def get_stored_password(conf):
    if not conf:
        return None
    b64 = conf.get('email_password_b64')
    if b64:
        try:
            return base64.b64decode(b64.encode('ascii')).decode('utf-8')
        except Exception:
            return None
    return None

# ---------------------------
# envio de e-mail (SMTP)
# ---------------------------
def send_log_by_email(email_conf, log_path, session_date, total, prompt_if_no_password=True):
    if not os.path.exists(log_path):
        raise FileNotFoundError("Log do dia não encontrado.")
    smtp_server = email_conf.get('smtp_server', 'smtp.gmail.com')
    smtp_port = int(email_conf.get('smtp_port', 587))
    email_from = email_conf.get('email_from')
    email_to = email_conf.get('email_to')
    if not email_from or not email_to:
        raise ValueError("E-mails não configurados corretamente.")
    password = get_stored_password(email_conf)
    if not password and prompt_if_no_password:
        print("Caso use Gmail, lembre-se de fornecer a *app password* (senha de app) — não a senha normal da conta.")
        password = getpass("Senha (app password) do e-mail do caixa (não será exibida): ").strip()
        if not password:
            raise ValueError("Senha não informada.")
        save_choice = input("Salvar senha para usos futuros? (s/N): ").strip().lower()
        if save_choice == 's':
            conf = load_email_config()
            conf['email_password_b64'] = base64.b64encode(password.encode('utf-8')).decode('ascii')
            save_email_config(conf)
    # montar mensagem
    msg = EmailMessage()
    msg['Subject'] = f"PandaCell - Log do Caixa {session_date}"
    msg['From'] = email_from
    msg['To'] = email_to
    body = ("PandaCell - Fechamento do Caixa\n\n"
            f"Data: {session_date}\n"
            f"Total do dia: R$ {money(total)}\n\n"
            "O log completo segue em anexo.\n")
    msg.set_content(body)
    with open(log_path, 'rb') as f:
        data = f.read()
        msg.add_attachment(data, maintype='text', subtype='plain', filename=os.path.basename(log_path))
    # tentativa de envio
    try:
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
                server.login(email_from, password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(email_from, password)
                server.send_message(msg)
        return True, None
    except Exception as e:
        return False, str(e)

def send_log_with_handling(email_conf, log_path, session_date, total):
    try:
        ok, err = send_log_by_email(email_conf, log_path, session_date, total, prompt_if_no_password=True)
        if ok:
            print("📧 Log enviado com sucesso para", email_conf.get('email_to'))
        else:
            print("❌ Falha no envio do e-mail:", err)
            save_failed_to_outbox(log_path, session_date, err)
    except Exception as ex:
        print("❌ Erro ao tentar enviar e-mail:", ex)
        save_failed_to_outbox(log_path, session_date, str(ex))

def save_failed_to_outbox(log_path, session_date, reason):
    # copia arquivo para outbox com metadata
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = os.path.basename(log_path)
        out_name = f"{session_date}_{ts}_{fname}"
        out_path = os.path.join(OUTBOX_DIR, out_name)
        shutil.copy2(log_path, out_path)
        # metadata
        meta = {
            'saved_at': datetime.now().isoformat(sep=' ', timespec='seconds'),
            'reason': reason,
            'original': os.path.basename(log_path)
        }
        atomic_write(out_path + ".meta.json", json.dumps(meta, indent=2, ensure_ascii=False))
        print("Cópia do log salva em outbox para reenvio:", out_path)
    except Exception as e:
        print("Falha ao salvar em outbox:", e)

def resend_outbox(email_conf):
    files = sorted([f for f in os.listdir(OUTBOX_DIR) if not f.endswith('.meta.json')])
    if not files:
        print("Nenhum item na fila (outbox).")
        return
    for fname in files:
        path = os.path.join(OUTBOX_DIR, fname)
        # infer session_date from filename prefix (we used session_date_ts_filename)
        try:
            parts = fname.split('_', 2)
            session_date = parts[0]
        except Exception:
            session_date = date.today().isoformat()
        print("\nTentando reenviar:", path)
        try:
            total = compute_total(session_date)  # tentativa: compute total from logs if exists
        except Exception:
            total = Decimal('0.00')
        ok, err = send_log_by_email(email_conf, path, session_date, total, prompt_if_no_password=True)
        if ok:
            print("Reenvio ok -> removendo item da outbox.")
            try:
                os.remove(path)
                meta = path + ".meta.json"
                if os.path.exists(meta):
                    os.remove(meta)
            except Exception:
                pass
        else:
            print("Falha no reenvio:", err)
            # manter no outbox para nova tentativa

# ---------------------------
# menus e fluxo principal
# ---------------------------
def show_menu(email_conf):
    from_email = email_conf.get('email_from', '<não configurado>')
    to_email = email_conf.get('email_to', '<não configurado>')
    print("\n--- PandaCell - Caixa ---")
    print(f"E-mail caixa(emissor): {from_email}    E-mail loja(destino): {to_email}")
    print("-------------------------")
    print("1) Registrar venda")
    print("2) Mostrar total do dia")
    print("3) Listar vendas (últimas)")
    print("4) Fechar caixa (finalizar dia) -> envia log por e-mail automaticamente")
    print("5) Reabrir sessão do dia (se estiver fechada) [AVANÇADO]")
    print("6) Mostrar top vendidos (semana)")
    print("7) Mostrar top vendidos (mês)")
    print("8) Painel de Destaques (top 5 semana + mês)")
    print("9) Configurar / Alterar e-mails")
    print("10) Definir / Alterar senha do e-mail do caixa (salva codificada)")
    print("11) Enviar log do dia agora")
    print("12) Reenviar itens da fila (outbox)")
    print("13) Gerenciar produtos (adicionar/editar/deletar/estoque)")
    print("0) Sair do programa (sem fechar caixa se estiver OPEN)")
    print("-------------------------")

def configure_emails_interactive_menu():
    conf = load_email_config()
    return prompt_email_setup(existing_conf=conf, ask_password=False)

# ---------------------------
# inicialização / main
# ---------------------------
def main():
    ensure_dirs()
    email_conf = load_email_config()
    # se não tiver e-mails, pedir (agora pede e-mail do caixa + senha primeiro, depois e-mail destino)
    if not email_conf.get('email_from') or not email_conf.get('email_to'):
        print("Parece que é a primeira vez executando o programa ou os e-mails não estão configurados.")
        email_conf = prompt_email_setup(existing_conf=email_conf, ask_password=True)
    today = start_session()
    auto_show_periodic_reports()
    while True:
        show_menu(email_conf)
        choice = input("Escolha: ").strip()
        # LIMPAR A TELA ANTES DE EXECUTAR A AÇÃO (evita acumular saídas antigas)
        clear_screen()
        if choice == '1':
            add_sale(today)
        elif choice == '2':
            total = compute_total(today)
            print(f"Total acumulado hoje ({today}): R$ {money(total)}")
        elif choice == '3':
            list_sales(today, show_lines=100)
        elif choice == '4':
            close_cash(today, email_conf)
        elif choice == '5':
            # função reopen_session não está definida no código original; se existir, ótimo.
            try:
                reopen_session(today)
            except NameError:
                print("Funcionalidade 'reabrir sessão' não disponível neste build.")
        elif choice == '6':
            show_top_week()
        elif choice == '7':
            show_top_month()
        elif choice == '8':
            show_panel(top_n=5)
        elif choice == '9':
            email_conf = configure_emails_interactive_menu()
        elif choice == '10':
            email_conf = configure_password_interactive()
        elif choice == '11':
            # enviar log do dia agora
            logp = get_log_path(today)
            total = compute_total(today)
            send_log_with_handling(email_conf, logp, today, total)
        elif choice == '12':
            resend_outbox(email_conf)
        elif choice == '13':
            manage_products_menu()
        elif choice == '0':
            print("Saindo do programa.")
            sys.exit(0)
        else:
            print("Opção inválida. Tente novamente.")

if __name__ == '__main__':
    main()
