# ChamaHub - Chama Management System

A comprehensive Django MVP that combines chama contributions and member loans, integrated with Payhero for seamless payment processing.

## Features

### Core Functionality
- **Member Registration & Authentication**: Secure user registration and login system
- **Contribution Management**: Track member contributions with payment processing
- **Loan Management**: Apply for loans, approval workflow, and disbursement tracking
- **Repayment Tracking**: Monitor loan repayments with progress indicators
- **Role-based Access**: Different dashboards for members and treasurers
- **Financial Calculations**: Automatic balance calculations and interest computations

### Payhero Integration
- **Payment Processing**: Secure payment handling through Payhero API
- **Webhook Support**: Automatic payment status updates via webhooks
- **Transaction Tracking**: Complete audit trail of all payments

## Project Structure

```
chamahub/
├── chamahub/          # Main Django project
│   ├── settings.py    # Project settings
│   ├── urls.py        # Main URL configuration
│   └── ...
├── core/              # Main application
│   ├── models.py      # Database models
│   ├── views.py       # View functions
│   ├── forms.py       # Django forms
│   ├── admin.py       # Admin interface
│   └── urls.py        # App URL configuration
├── templates/         # HTML templates
│   └── core/
├── static/           # Static files (CSS, JS, images)
└── manage.py         # Django management script
```

## Models

### Contribution
- Stores member contributions with Payhero references
- Tracks payment status (pending, confirmed, failed)
- Links to member and includes notes

### Loan
- Manages loan applications and approvals
- Calculates total due amount with interest
- Tracks approval and disbursement status
- Includes repayment period and purpose

### Repayment
- Records loan repayments
- Links to Payhero transactions
- Tracks payment status and progress

### ChamaProfile
- Extended user profile with roles
- Supports member, treasurer, and chairperson roles
- Stores additional member information

## Key Features

### Member Dashboard
- Personal balance overview
- Contribution and loan history
- Repayment progress tracking
- Quick action buttons for common tasks

### Treasurer Dashboard
- Group balance and financial overview
- Pending loan approvals
- Member balance summaries
- Recent transaction monitoring

### Payment Integration
- Payhero payment buttons for contributions and repayments
- Webhook endpoint for payment confirmations
- Automatic status updates

## Installation & Setup

1. **Clone and Setup**:
   ```bash
   cd /home/korr1e/Payhero/chamahub
   ```

2. **Install Dependencies**:
   ```bash
   pip install django
   ```

3. **Run Migrations**:
   ```bash
   python3 manage.py migrate
   ```

4. **Create Superuser**:
   ```bash
   python3 manage.py createsuperuser
   ```

5. **Start Development Server**:
   ```bash
   python3 manage.py runserver
   ```

6. **Access the Application**:
   - Home: http://localhost:8000/
   - Admin: http://localhost:8000/admin/
   - Login: admin / admin123 (default superuser)

## Usage Guide

### For Members

1. **Register**: Create an account on the home page
2. **Login**: Access your personal dashboard
3. **Make Contributions**: Use the "Make Contribution" button
4. **Apply for Loans**: Submit loan applications with purpose and terms
5. **Make Repayments**: Pay back loans with progress tracking

### For Treasurers

1. **Login**: Access treasurer dashboard (requires treasurer role)
2. **Review Loans**: Approve or reject pending loan applications
3. **Disburse Loans**: Release approved loans to members
4. **Monitor Group**: Track group balance and member activities

## Payhero Integration Points

### Payment Processing
The following functions need to be connected to the actual Payhero API:

1. **`initiate_payhero_payment()`** in `views.py`:
   - Replace with actual Payhero API calls
   - Generate payment URLs
   - Handle redirects to Payhero payment page

2. **`payhero_webhook()`** in `views.py`:
   - Validate Payhero webhook signatures
   - Process payment confirmations
   - Update contribution/repayment status

### Configuration
Add Payhero credentials to `settings.py`:
```python
PAYHERO_API_KEY = 'your_api_key'
PAYHERO_SECRET_KEY = 'your_secret_key'
PAYHERO_BASE_URL = 'https://api.payhero.com'
```

## Database Schema

### Key Relationships
- User → Contributions (One-to-Many)
- User → Loans (One-to-Many)
- Loan → Repayments (One-to-Many)
- User → ChamaProfile (One-to-One)

### Important Calculations
- **Group Balance**: Total confirmed contributions - Total disbursed loans
- **Member Balance**: Member's contributions - Member's disbursed loans
- **Total Due**: Principal + (Principal × Interest Rate / 100)
- **Repayment Progress**: (Total repaid / Total due) × 100

## Security Features

- CSRF protection on all forms
- User authentication and authorization
- Role-based access control
- Secure payment processing through Payhero
- Input validation and sanitization

## Development Notes

### TODO: Payhero Integration
1. Replace payment stubs with actual Payhero API calls
2. Implement webhook signature validation
3. Add error handling for payment failures
4. Implement retry logic for failed payments

### Future Enhancements
- Email notifications for loan approvals
- SMS alerts for payment confirmations
- Advanced reporting and analytics
- Mobile app integration
- Multi-currency support

## Testing

The application includes:
- Form validation
- Role-based access control
- Payment status tracking
- Balance calculations
- Webhook processing

## Support

For questions or issues:
1. Check the Django admin interface for data management
2. Review the webhook logs for payment processing
3. Verify Payhero API integration points
4. Check user roles and permissions

## License

This is a development MVP for demonstration purposes.
