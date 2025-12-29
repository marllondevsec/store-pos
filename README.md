inicio
# store-pos

**store-pos** is a simple Point of Sale (POS) system written in Python, based on text and JSON files, with real-time saving, product management, automated reports, and automatic email delivery of daily logs when closing the cash register.

It is designed for small businesses and allows resuming open sessions even after unexpected program termination.

---

## ✨ Features

* 📦 **Product management**

  * Add, edit, and delete products
  * Set unit prices
  * Stock control (add, remove, or define quantity)

* 💰 **Real-time sales recording**

  * Each sale is saved immediately to disk
  * Automatic cash session recovery if the program is closed
  * Catalog integration (price suggestion and stock update)

* 📊 **Reports and aggregation**

  * Daily sales total
  * Top-selling products (weekly)
  * Top-selling products (monthly)
  * Highlights dashboard (top 5 weekly and monthly)
  * Automatic generation:

    * Weekly report (every Saturday)
    * Monthly report (last day of the month)

* 🧾 **Logs and auditability**

  * Daily logs stored in `logs/`
  * Automatic summary files
  * Unique identifier per sale (short UUID)

* 📧 **Email log delivery**

  * Cashier email (sender) and store email (recipient)
  * SMTP support (e.g., Gmail)
  * Automatic sending on cash close
  * Manual resend option
  * Retry queue (`outbox/`) on failure
  * Optional password storage (Base64-encoded)

---

## 🗂 Project Structure

```
store-pos/
├── store_pos.py
├── products.json        # Product catalog
├── current_session.txt  # Current session state
├── email_config.json    # Email configuration
├── logs/                # Daily logs and reports
│   ├── PandaCell_YYYY-MM-DD.txt
│   ├── *_summary_week_*.txt
│   └── *_summary_month_*.txt
├── outbox/              # Pending email deliveries
```

---

## 🚀 Requirements

* Python **3.6+**
* Python standard library only (no external dependencies)

---

## ▶️ How to Run

```bash
python3 store_pos.py
```

On first run, the system will ask for:

1. Cashier email (sender)
2. Email password (optional – recommended to use an *app password*)
3. Store email (recipient)

These settings are saved in `email_config.json`.

---

## 🧭 Main Menu

* Register sales
* View daily total
* List sales
* Close cash register (automatically sends the log)
* Weekly and monthly reports
* Highlights dashboard
* Email configuration
* Product management

---

## 🔐 Security Notes

* Email passwords are stored only if the user explicitly allows it
* Passwords are Base64-encoded (not encryption)
* Strongly recommended to use **App Passwords** for providers like Gmail

---

## 📌 Notes

* The system **does not use a database**, only local files
* Ideal for local usage, small shops, or test environments
* Easy to audit, customize, and extend

---

## 📄 License

This project is distributed as **free software for educational and commercial purposes**, without any warranty.

You are free to use, modify, and adapt it as needed.

---

**store-pos** — simple, reliable, and auditable.
