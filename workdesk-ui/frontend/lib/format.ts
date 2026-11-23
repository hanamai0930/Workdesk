/** Display money without changing the source amount used for calculations. */
export function formatMoney(value: string | number | null | undefined, currency = 'CNY'): string {
  if (value == null || typeof value === 'string' && !value.trim()) return '—';
  const amount = Number(value);
  if (!Number.isFinite(amount)) return '—';
  const digits = Math.abs(amount) >= 1 ? 2 : 4;
  const options: Intl.NumberFormatOptions = {
    style: 'currency', currency, currencyDisplay: 'narrowSymbol',
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  };
  let display: (amount: number) => string;
  try { const formatter = new Intl.NumberFormat('zh-CN', options); display = amount => formatter.format(amount); }
  catch { display = amount => currency + ' ' + amount.toFixed(digits); }
  if (amount > 0 && amount < 0.0001) return '<' + display(0.0001);
  if (amount < 0 && amount > -0.0001) return '>' + display(-0.0001);
  return display(amount);
}
