export function loadGestureMotions(owner) {
  const times = [0, 0.16, 0.48, 0.82, 1.12, 1.55];
  const channels = owner.adapter.nod;
  const Curves = Object.entries(channels).map(([Id, values]) => {
    const Segments = [0, values[0]];
    for (let i = 1; i < times.length; i++) {
      const start = times[i - 1];
      const span = times[i] - start;
      Segments.push(
        1,
        start + span / 3,
        values[i - 1],
        start + (2 * span) / 3,
        values[i],
        times[i],
        values[i],
      );
    }
    return { Target: "Parameter", Id, Segments };
  });
  const data = new TextEncoder().encode(
    JSON.stringify({
      Version: 3,
      Meta: {
        Duration: times.at(-1),
        Fps: 30,
        Loop: false,
        AreBeziersRestricted: true,
        CurveCount: Curves.length,
        TotalSegmentCount: Curves.length * (times.length - 1),
        TotalPointCount: Curves.length * (1 + 3 * (times.length - 1)),
        UserDataCount: 0,
        TotalUserDataSize: 0,
      },
      Curves,
    }),
  ).buffer;
  owner.workActions.load(owner, "Nod", data);
}
