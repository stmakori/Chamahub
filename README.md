# ChamaHub

Chama (savings group) management for Kenya: member contributions, loans, repayments and
withdrawals, with M-Pesa payments through **Payhero** and a tamper-evident audit trail recorded on
the **Stellar** blockchain.

Chamas run on trust, but most still track money in notebooks and WhatsApp groups. ChamaHub gives
every member a live view of their balance and the group's books, and gives treasurers a clear
approval workflow.

## Features

- **Members**: register, contribute, apply for loans, repay, request withdrawals, and track payment status live
- **Treasurers**: group balance overview, loan approvals, payment confirmation (single and bulk)
- **Payments**: M-Pesa STK push via Payhero, with webhook confirmations and a status page
- **Blockchain audit trail**: confirmed transactions are recorded on Stellar (testnet by default) and viewable in a blockchain dashboard; Stellar secret keys are stored encrypted
- **Accountability**: audit log of sensitive actions, role-based access (member, treasurer, chairperson)
- **Interest and balances**: automatic totals for loans (principal + interest), repayment progress and group balance

## Tech stack

Django 4.2+ · Python · SQLite (local) or PostgreSQL via `DATABASE_URL` · Payhero API · Stellar SDK · `cryptography` (Fernet)

## Run locally

```bash
git clone https://github.com/stmakori/Chamahub.git
cd Chamahub/chamahub
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # fill in Payhero credentials; Stellar is optional
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open http://localhost:8000. Give a user the treasurer role from the Django admin (`/admin/`) to see
the treasurer dashboard.

Payments need Payhero credentials and a public `PAYHERO_CALLBACK_URL` (for example via ngrok) to
receive webhooks. To enable the blockchain trail, set `STELLAR_ENABLED=True` with a **testnet**
account and an encryption key.

## How balances are calculated

- **Group balance** = confirmed contributions − disbursed loans
- **Member balance** = member's contributions − member's disbursed loans
- **Total due** = principal + principal × interest rate / 100
- **Repayment progress** = total repaid / total due × 100

## Roadmap

- Webhook signature validation and retries for failed payments
- SMS and email notifications for approvals and confirmations
- Reports and exports for chama meetings
