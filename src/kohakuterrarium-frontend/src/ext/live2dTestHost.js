import { createMockHost as createHost } from "@/ext/sdk/mock"

// An in-memory HTTP peer for package tests; UI/controller code remains real.
export function controllerRoutes() {
  let controller = null,
    generation = 0
  const conflict = () => {
    throw { response: { status: 409, data: { detail: { controller } } } }
  }
  return {
    "GET /controller": () => ({ controller }),
    "POST /controller": ({ data: { client, token, operation } }) => {
      const owns = controller?.client === client && controller?.token === token
      if (operation === "release") {
        if (owns) controller = null
      } else {
        if (operation === "renew" && !owns) conflict()
        if (operation === "claim" && controller && !owns) conflict()
        if (operation === "takeover" && (controller?.token || "") !== token) conflict()
        if (!owns) controller = { client, token: `owner-${++generation}` }
      }
      return { controller }
    },
  }
}

export function createMockHost(options = {}) {
  return createHost({ ...options, routes: { ...controllerRoutes(), ...options.routes } })
}
