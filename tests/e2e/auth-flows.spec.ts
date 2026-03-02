import { test, expect } from '@playwright/test';

const BASE_URL = 'https://grandcom-pro-signals.preview.emergentagent.com';
const ADMIN_EMAIL = 'admin@forexsignals.com';
const ADMIN_PASSWORD = 'Admin@2024!Forex';

test.describe('Authentication & Login Flow', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
  });

  test('should display login form on homepage', async ({ page }) => {
    // Check for login form elements
    await expect(page.locator('input[placeholder="Email"]')).toBeVisible();
    await expect(page.locator('input[placeholder="Password"]')).toBeVisible();
    await expect(page.locator('text=Sign In')).toBeVisible();
    await expect(page.locator('text=Sign Up')).toBeVisible();
    await expect(page.locator('text=Grandcom Forex Signals Pro')).toBeVisible();
  });

  test('should login admin user successfully', async ({ page }) => {
    // Fill login form
    await page.fill('input[placeholder="Email"]', ADMIN_EMAIL);
    await page.fill('input[placeholder="Password"]', ADMIN_PASSWORD);
    await page.click('text=Sign In');
    
    // Wait for navigation to home
    await page.waitForLoadState('domcontentloaded');
    await expect(page.locator('text=Welcome back')).toBeVisible({ timeout: 10000 });
    await expect(page.locator('text=Admin User')).toBeVisible();
  });

  test('should show error for invalid credentials', async ({ page }) => {
    await page.fill('input[placeholder="Email"]', 'invalid@email.com');
    await page.fill('input[placeholder="Password"]', 'wrongpassword');
    await page.click('text=Sign In');
    
    // Should still be on login page or show error
    await page.waitForLoadState('domcontentloaded');
    // Login page elements should still be visible (login failed)
    const loginButton = page.locator('text=Sign In').first();
    await expect(loginButton).toBeVisible({ timeout: 5000 });
  });
});

test.describe('Admin Panel Button - Role Synchronization', () => {
  test('should show Admin Panel button for admin user on profile page', async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    
    // Login as admin
    await page.fill('input[placeholder="Email"]', ADMIN_EMAIL);
    await page.fill('input[placeholder="Password"]', ADMIN_PASSWORD);
    await page.click('text=Sign In');
    await page.waitForLoadState('domcontentloaded');
    
    // Wait for home to load
    await expect(page.locator('text=Welcome back')).toBeVisible({ timeout: 10000 });
    
    // Navigate to profile
    await page.goto('/profile', { waitUntil: 'domcontentloaded' });
    
    // Check Admin Panel button is visible
    await expect(page.locator('text=Admin Panel')).toBeVisible({ timeout: 10000 });
    await expect(page.locator('text=ADMIN Plan')).toBeVisible();
  });

  test('should navigate to admin page when Admin Panel is clicked', async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    
    // Login as admin
    await page.fill('input[placeholder="Email"]', ADMIN_EMAIL);
    await page.fill('input[placeholder="Password"]', ADMIN_PASSWORD);
    await page.click('text=Sign In');
    await page.waitForLoadState('domcontentloaded');
    await expect(page.locator('text=Welcome back')).toBeVisible({ timeout: 10000 });
    
    // Navigate to profile
    await page.goto('/profile', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('text=Admin Panel')).toBeVisible({ timeout: 10000 });
    
    // Click Admin Panel
    await page.click('text=Admin Panel');
    await page.waitForLoadState('domcontentloaded');
    
    // Should be on admin page
    await expect(page).toHaveURL(/admin/);
  });
});

test.describe('Home Page after Login', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await page.fill('input[placeholder="Email"]', ADMIN_EMAIL);
    await page.fill('input[placeholder="Password"]', ADMIN_PASSWORD);
    await page.click('text=Sign In');
    await page.waitForLoadState('domcontentloaded');
    await expect(page.locator('text=Welcome back')).toBeVisible({ timeout: 10000 });
  });

  test('should display ML Engine status', async ({ page }) => {
    await expect(page.locator('text=ML Engine Active')).toBeVisible();
  });

  test('should display statistics cards', async ({ page }) => {
    await expect(page.locator('text=Total Signals')).toBeVisible();
    await expect(page.getByText('Active', { exact: true })).toBeVisible();
    await expect(page.locator('text=Win Rate')).toBeVisible();
  });

  test('should display recent signals section', async ({ page }) => {
    await expect(page.locator('text=Recent Signals')).toBeVisible();
  });
});
