import { AddOn, Plan } from '../../services/billing.service';
import { clampMonths, dependentsOf, toggleAddon, totalCopCents } from './billing-pricing';

const plan: Plan = {
  key: 'business', name: 'Business', price_usd_cents: 14900, price_cop_cents: 59_600_000, interval: 'month',
  features: [], meetings_per_month: 100, users_included: null, is_public: true, is_active: true, sort_order: 2,
};
const addon = (key: string, cop: number, requires: string[] = []): AddOn => ({
  key, name: key, price_usd_cents: 0, price_cop_cents: cop, features: [], requires, is_active: true, sort_order: 1,
});
const catalog = [addon('owned_bot', 11_600_000), addon('video_recording', 7_600_000, ['owned_bot']), addon('ask_ai', 7_600_000)];

describe('billing pricing helpers', () => {
  it('multiplies plan plus add-ons by the months, clamped to 1–12', () => {
    expect(totalCopCents(plan, [catalog[0]], 3)).toBe((59_600_000 + 11_600_000) * 3);
    expect(totalCopCents(plan, [], 0)).toBe(59_600_000);
    expect(totalCopCents(plan, [], 40)).toBe(59_600_000 * 12);
    expect(totalCopCents(null, [catalog[0]], 2)).toBe(0);
    expect(clampMonths(Number.NaN)).toBe(1);
  });

  it('selecting an add-on pulls in what it requires', () => {
    const next = toggleAddon(new Set(), 'video_recording', catalog);
    expect([...next].sort()).toEqual(['owned_bot', 'video_recording']);
  });

  it('removing a requirement drops the add-ons that depended on it', () => {
    const selected = new Set(['owned_bot', 'video_recording', 'ask_ai']);
    expect(dependentsOf('owned_bot', selected, catalog)).toEqual(['video_recording']);
    const next = toggleAddon(selected, 'owned_bot', catalog);
    expect([...next]).toEqual(['ask_ai']);
    expect(selected.size).toBe(3);
  });
});
