/* View index — the router resolves view names here. */

export { render as dashboard, refresh as refreshDashboard } from './dashboard.js';
export { render as agents, refresh as refreshAgents } from './agents.js';
export { render as agent, refresh as refreshAgent } from './agent.js';
export { render as communications, refresh as refreshCommunications } from './comms.js';
export { render as conversations, refresh as refreshConversations } from './conversations.js';
export { render as tasks, refresh as refreshTasks } from './tasks.js';
export { render as activity, refresh as refreshActivity } from './activity.js';
export { render as organization, refresh as refreshOrganization } from './org.js';
export { render as server, refresh as refreshServer } from './server.js';
