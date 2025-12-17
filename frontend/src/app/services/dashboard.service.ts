import { Injectable } from '@angular/core';
import { Dashboard, DashboardItem } from '../models/dashboard.models';

const STORAGE_KEY = 'ai_dashboards';
const STORAGE_ITEMS_KEY = 'ai_dashboard_items';

@Injectable({
  providedIn: 'root',
})
export class DashboardService {
  private getDashboards(): Dashboard[] {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored ? JSON.parse(stored) : [];
  }

  private saveDashboards(dashboards: Dashboard[]): void {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(dashboards));
  }

  private getItems(): DashboardItem[] {
    const stored = localStorage.getItem(STORAGE_ITEMS_KEY);
    return stored ? JSON.parse(stored) : [];
  }

  private saveItems(items: DashboardItem[]): void {
    localStorage.setItem(STORAGE_ITEMS_KEY, JSON.stringify(items));
  }

  getAllDashboards(): Dashboard[] {
    return this.getDashboards();
  }

  getDashboard(id: string): Dashboard | null {
    const dashboards = this.getDashboards();
    return dashboards.find(d => d.id === id) || null;
  }

  createDashboard(name: string): Dashboard {
    const dashboards = this.getDashboards();
    const dashboard: Dashboard = {
      id: `dashboard_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
      name,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    dashboards.push(dashboard);
    this.saveDashboards(dashboards);
    return dashboard;
  }

  updateDashboard(id: string, updates: Partial<Dashboard>): Dashboard | null {
    const dashboards = this.getDashboards();
    const index = dashboards.findIndex(d => d.id === id);
    if (index === -1) return null;
    
    dashboards[index] = {
      ...dashboards[index],
      ...updates,
      updated_at: new Date().toISOString(),
    };
    this.saveDashboards(dashboards);
    return dashboards[index];
  }

  deleteDashboard(id: string): boolean {
    const dashboards = this.getDashboards();
    const filtered = dashboards.filter(d => d.id !== id);
    if (filtered.length === dashboards.length) return false;
    
    this.saveDashboards(filtered);
    
    // Also delete all items for this dashboard
    const items = this.getItems();
    const filteredItems = items.filter(item => item.dashboard_id !== id);
    this.saveItems(filteredItems);
    
    return true;
  }

  getDashboardItems(dashboardId: string): DashboardItem[] {
    const items = this.getItems();
    return items.filter(item => item.dashboard_id === dashboardId);
  }

  addItem(item: Omit<DashboardItem, 'id' | 'created_at' | 'updated_at'>): DashboardItem {
    const items = this.getItems();
    const newItem: DashboardItem = {
      ...item,
      id: `item_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    items.push(newItem);
    this.saveItems(items);
    return newItem;
  }

  updateItem(id: string, updates: Partial<DashboardItem>): DashboardItem | null {
    const items = this.getItems();
    const index = items.findIndex(item => item.id === id);
    if (index === -1) return null;
    
    items[index] = {
      ...items[index],
      ...updates,
      updated_at: new Date().toISOString(),
    };
    this.saveItems(items);
    return items[index];
  }

  deleteItem(id: string): boolean {
    const items = this.getItems();
    const filtered = items.filter(item => item.id !== id);
    if (filtered.length === items.length) return false;
    
    this.saveItems(filtered);
    return true;
  }
}

