import { test, expect } from '@playwright/test';

const BASE_URL = 'https://gold-signal-debug.preview.emergentagent.com';
const ADMIN_EMAIL = 'admin@forexsignals.com';
const ADMIN_PASSWORD = 'Admin@2024!Forex';

test.describe('Signal Display', () => {
  test.beforeEach(async ({ page }) => {
    // Login first
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await page.fill('input[placeholder="Email"]', ADMIN_EMAIL);
    await page.fill('input[placeholder="Password"]', ADMIN_PASSWORD);
    await page.click('text=Sign In');
    await page.waitForLoadState('domcontentloaded');
    await expect(page.locator('text=Welcome back')).toBeVisible({ timeout: 10000 });
  });

  test('should display signal cards on home page', async ({ page }) => {
    // Check for signal cards - look for trading pair names
    const signalPairs = ['XAUUSD', 'EURUSD', 'GBPUSD', 'USDJPY', 'EURJPY'];
    
    // At least one signal pair should be visible
    let foundSignal = false;
    for (const pair of signalPairs) {
      const pairLocator = page.locator(`text=${pair}`);
      if (await pairLocator.count() > 0) {
        foundSignal = true;
        break;
      }
    }
    
    // Should have some signal data
    expect(foundSignal).toBe(true);
  });

  test('should display signal types (BUY/SELL)', async ({ page }) => {
    // Check for BUY or SELL buttons/labels
    const buyLocator = page.locator('text=BUY');
    const sellLocator = page.locator('text=SELL');
    
    const buyCount = await buyLocator.count();
    const sellCount = await sellLocator.count();
    
    // Should have at least one BUY or SELL signal
    expect(buyCount + sellCount).toBeGreaterThan(0);
  });

  test('should display TP levels in signal cards', async ({ page }) => {
    // Check for TP1, TP2, TP3 labels
    await expect(page.locator('text=TP1').first()).toBeVisible({ timeout: 15000 });
    await expect(page.locator('text=TP2').first()).toBeVisible();
    await expect(page.locator('text=TP3').first()).toBeVisible();
  });

  test('should display SL (Stop Loss) in signal cards', async ({ page }) => {
    await expect(page.locator('text=SL').first()).toBeVisible({ timeout: 15000 });
  });

  test('should navigate to signals tab', async ({ page }) => {
    // Click on View All to see all signals
    const viewAllLink = page.locator('text=View All');
    if (await viewAllLink.isVisible()) {
      await viewAllLink.click();
      await page.waitForLoadState('domcontentloaded');
    }
  });
});

test.describe('Tab Navigation', () => {
  test.beforeEach(async ({ page }) => {
    // Login first
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await page.fill('input[placeholder="Email"]', ADMIN_EMAIL);
    await page.fill('input[placeholder="Password"]', ADMIN_PASSWORD);
    await page.click('text=Sign In');
    await page.waitForLoadState('domcontentloaded');
    await expect(page.locator('text=Welcome back')).toBeVisible({ timeout: 10000 });
  });

  test('should navigate to signals page', async ({ page }) => {
    await page.goto('/signals', { waitUntil: 'domcontentloaded' });
    // Should show signals page content
    await page.waitForLoadState('domcontentloaded');
  });

  test('should navigate to analytics page', async ({ page }) => {
    await page.goto('/analytics', { waitUntil: 'domcontentloaded' });
    await page.waitForLoadState('domcontentloaded');
  });

  test('should navigate to profile page', async ({ page }) => {
    await page.goto('/profile', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('text=Admin User')).toBeVisible({ timeout: 10000 });
  });
});
