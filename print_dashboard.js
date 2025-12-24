// Quick script to print dashboard data from localStorage
// Run this in your browser console on the dashboard page

const STORAGE_KEY = 'ai_dashboards';
const STORAGE_ITEMS_KEY = 'ai_dashboard_items';

// Get all dashboards
const dashboards = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
console.log('=== ALL DASHBOARDS ===');
console.log(JSON.stringify(dashboards, null, 2));

// Get all dashboard items
const items = JSON.parse(localStorage.getItem(STORAGE_ITEMS_KEY) || '[]');
console.log('\n=== ALL DASHBOARD ITEMS ===');
console.log(JSON.stringify(items, null, 2));

// Print first dashboard with its items
if (dashboards.length > 0) {
  const firstDashboard = dashboards[0];
  console.log('\n=== FIRST DASHBOARD ===');
  console.log(JSON.stringify(firstDashboard, null, 2));
  
  const dashboardItems = items.filter(item => item.dashboard_id === firstDashboard.id);
  console.log(`\n=== ITEMS FOR DASHBOARD "${firstDashboard.name}" (${dashboardItems.length} items) ===`);
  dashboardItems.forEach((item, index) => {
    console.log(`\n--- Item ${index + 1} ---`);
    console.log(JSON.stringify(item, null, 2));
  });
}

