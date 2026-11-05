# verify/

`db.py` gives read-only access to Fakturama's HSQLDB over JDBC (via jaydebeapi
and the JRE bundled with Fakturama).

## Rules

- **Read-only.** `query()` rejects anything that is not a SELECT, and the
  connection is set read-only.
- **Exclude soft-deleted rows.** Fakturama soft-deletes, so always filter
  `live()` (`DELETED` false). A deleted Order does not block re-booking the
  same Cust.Ref.
- **Debtors.** `FKT_CONTACT.DTYPE = 'Debitor'`. Addresses are in `FKT_ADDRESS`
  (`CONTACT_ADDRESSES` is the FK), and roles are in `FKT_ADDRESS_CONTACTTYPES`
  (BILLING / DELIVERY).
- **Products.** `FKT_PRODUCT.PRICE1` is the **net** price. For products created
  from a rounded gross price it carries a remainder (9.901176…).
- **VAT.** `FKT_VAT.TAXVALUE` is a fraction (0.19), and `CODE` is the e-invoice
  code (`S`).
- **Payments.** `FKT_PAYMENT.CODE` uses UNTDID 4461: 30 credit transfer, 54
  credit card, 59 SEPA direct debit.
- **Settings.** Fakturama also keeps its settings in `FKT_USERPROPERTY`
  (`T_VALUE`), for example `PREFERENCE_CURRENCY_LOCALE`. Change them through
  File → Preferences, not by editing the prefs file. The test workspace uses
  `de/DE` (EUR, `1.234,57`); the prefs file and the database both say so.
