import { createRouter, createWebHistory } from "vue-router";
import { useAuthStore } from "../stores/auth";
import AssessmentReportView from "../views/AssessmentReportView.vue";
import AssessmentSessionView from "../views/AssessmentSessionView.vue";
import AssessmentStartView from "../views/AssessmentStartView.vue";
import DashboardView from "../views/DashboardView.vue";
import LoginView from "../views/LoginView.vue";
import RubricView from "../views/RubricView.vue";
import ScenarioView from "../views/ScenarioView.vue";
import SessionReviewView from "../views/SessionReviewView.vue";
import SessionListView from "../views/SessionListView.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: "/",
      redirect: "/assessment",
    },
    {
      path: "/assessment",
      name: "assessment-start",
      component: AssessmentStartView,
      meta: { public: true },
    },
    {
      path: "/assessment/session/:sessionUuid",
      name: "assessment-session",
      component: AssessmentSessionView,
      meta: { public: true },
    },
    {
      path: "/assessment/report/:sessionUuid",
      name: "assessment-report",
      component: AssessmentReportView,
      meta: { public: true },
    },
    {
      path: "/admin/login",
      name: "login",
      component: LoginView,
      meta: { public: true },
    },
    {
      path: "/admin/dashboard",
      name: "dashboard",
      component: DashboardView,
    },
    {
      path: "/admin/rubrics",
      name: "rubrics",
      component: RubricView,
    },
    {
      path: "/admin/scenarios",
      name: "scenarios",
      component: ScenarioView,
    },
    {
      path: "/admin/sessions",
      name: "admin-sessions",
      component: SessionListView,
    },
    {
      path: "/admin/sessions/:sessionUuid",
      name: "admin-session-review",
      component: SessionReviewView,
    },
  ],
});

router.beforeEach(async (to) => {
  const auth = useAuthStore();
  if (!to.meta.public) {
    if (!auth.isLoggedIn) {
      return "/admin/login";
    }
    if (!auth.sessionValidated && !(await auth.validateSession())) {
      return "/admin/login";
    }
  }
  if (to.name === "login" && auth.isLoggedIn) {
    if (!auth.sessionValidated && !(await auth.validateSession())) {
      return true;
    }
    return "/admin/dashboard";
  }
  return true;
});
