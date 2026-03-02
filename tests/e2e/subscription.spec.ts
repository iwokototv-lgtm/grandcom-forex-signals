import { test, expect } from '@playwright/test';

const BASE_URL = 'https://grandcom-pro-signals.preview.emergentagent.com';
const ADMIN_EMAIL = 'admin@forexsignals.com';
const ADMIN_PASSWORD = 'Admin@2024!Forex';

test.describe('Subscription Page', () => {
  test.beforeEach(async ({ page }) => {
    // Login first
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await page.fill('input[placeholder="Email"]', ADMIN_EMAIL);
    await page.fill('input[placeholder="Password"]', ADMIN_PASSWORD);
    await page.click('text=Sign In');
    await page.waitForLoadState('domcontentloaded');
    await expect(page.locator('text=Welcome back')).toBeVisible({ timeout: 10000 });
  });

  test('should navigate to subscription page from profile', async ({ page }) => {
    // Go to profile
    await page.goto('/profile', { waitUntil: 'domcontentloaded' });
    
    // Click Manage Subscription button
    await expect(page.locator('text=Manage Subscription')).toBeVisible({ timeout: 10000 });
    await page.click('text=Manage Subscription');
    
    await page.waitForLoadState('domcontentloaded');
    await expect(page.locator('text=Subscription Plans')).toBeVisible({ timeout: 10000 });
  });

  test('should display subscription page with plans', async ({ page }) => {
    await page.goto('/subscription', { waitUntil: 'domcontentloaded' });
    
    // Wait for page to load
    await expect(page.locator('text=Subscription Plans')).toBeVisible({ timeout: 10000 });
    
    // Check Current Plan section
    await expect(page.locator('text=Current Plan')).toBeVisible();
    
    // Check Why Upgrade section
    await expect(page.locator('text=Why Upgrade?')).toBeVisible();
    
    // Check Monthly Plans section
    await expect(page.locator('text=Monthly Plans')).toBeVisible();
  });

  test('should display Pro Monthly plan with correct price', async ({ page }) => {
    await page.goto('/subscription', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('text=Subscription Plans')).toBeVisible({ timeout: 10000 });
    
    // Check Pro Monthly
    await expect(page.locator('text=Pro Monthly')).toBeVisible();
    await expect(page.locator('text=$29.99')).toBeVisible();
  });

  test('should display Premium Monthly plan with MOST POPULAR badge', async ({ page }) => {
    await page.goto('/subscription', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('text=Subscription Plans')).toBeVisible({ timeout: 10000 });
    
    // Check Premium Monthly
    await expect(page.locator('text=Premium Monthly')).toBeVisible();
    await expect(page.locator('text=$79.99')).toBeVisible();
    await expect(page.getByText('MOST POPULAR').first()).toBeVisible();
  });

  test('should show Subscribe Now buttons', async ({ page }) => {
    await page.goto('/subscription', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('text=Subscription Plans')).toBeVisible({ timeout: 10000 });
    
    // Check for subscribe buttons
    const subscribeButtons = page.locator('text=Subscribe Now');
    await expect(subscribeButtons.first()).toBeVisible();
  });

  test('should show benefit features', async ({ page }) => {
    await page.goto('/subscription', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('text=Subscription Plans')).toBeVisible({ timeout: 10000 });
    
    // Check benefits - use first() to avoid strict mode violations
    await expect(page.getByText('Advanced ML Analytics', { exact: true })).toBeVisible();
    await expect(page.getByText('Push Notifications', { exact: true })).toBeVisible();
  });
});
