import { beforeEach, describe, expect, it } from "vitest";
import {
  clearAuth,
  getJwt,
  getUser,
  isAuthenticated,
  setAuth,
  type AuthUser,
} from "@/lib/auth";

const sampleUser: AuthUser = {
  id: "a0000001-0000-4000-8000-000000000001",
  name: "张三",
  dept_path: "/HR/招聘组",
  is_active: true,
};

describe("lib/auth sessionStorage helpers", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it("setAuth stores jwt + user; getJwt + getUser read them back", () => {
    setAuth("jwt-token-abc", sampleUser);
    expect(getJwt()).toBe("jwt-token-abc");
    expect(getUser()).toEqual(sampleUser);
    expect(isAuthenticated()).toBe(true);
  });

  it("clearAuth empties both jwt and user; isAuthenticated returns false", () => {
    setAuth("jwt-token-abc", sampleUser);
    clearAuth();
    expect(getJwt()).toBeNull();
    expect(getUser()).toBeNull();
    expect(isAuthenticated()).toBe(false);
  });

  it("getUser returns null when stored user JSON is malformed", () => {
    sessionStorage.setItem("ncmu_jwt", "abc");
    sessionStorage.setItem("ncmu_user", "{not-json");
    expect(getUser()).toBeNull();
    // jwt is still readable, but isAuthenticated requires both → false
    expect(getJwt()).toBe("abc");
    expect(isAuthenticated()).toBe(false);
  });
});
