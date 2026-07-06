use crate::*;
use svg::node::element::{Circle, Group, Line, Polyline, Rectangle, Style, Text, Title};

/// 0 <= val <= 1
pub fn color(mut val: f64) -> String {
    val.setmin(1.0);
    val.setmax(0.0);

    let h = (360.0 * val).rem_euclid(360.0);
    let s = 0.75;
    let v = 0.95;

    let c = v * s;
    let x = c * (1.0 - ((h / 60.0) % 2.0 - 1.0).abs());
    let m = v - c;

    let (rp, gp, bp) = if h < 60.0 {
        (c, x, 0.0)
    } else if h < 120.0 {
        (x, c, 0.0)
    } else if h < 180.0 {
        (0.0, c, x)
    } else if h < 240.0 {
        (0.0, x, c)
    } else if h < 300.0 {
        (x, 0.0, c)
    } else {
        (c, 0.0, x)
    };

    let r = ((rp + m) * 255.0).round() as i32;
    let g = ((gp + m) * 255.0).round() as i32;
    let b = ((bp + m) * 255.0).round() as i32;

    format!("#{:02x}{:02x}{:02x}", r, g, b)
}

pub fn rect(x: usize, y: usize, w: usize, h: usize, fill: &str) -> Rectangle {
    Rectangle::new()
        .set("x", x)
        .set("y", y)
        .set("width", w)
        .set("height", h)
        .set("fill", fill)
}

pub fn group(title: String) -> Group {
    Group::new().add(Title::new(title))
}

pub fn vis_default(input: &Input, out: &Output) -> (i64, String, String) {
    let (mut score, err, svg) = vis(input, out, usize::MAX);
    if !err.is_empty() {
        score = 0;
    }
    (score, err, svg)
}

// https://dotown.maeda-design-room.net/1670/
const HERO_PNG: &str = concat!(
    "data:image/png;charset=utf-8;base64,",
    "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"
);

// https://www.svgrepo.com/svg/321509/stone-throne
const GOAL_SVG_PATH: &str = "M255.563 21.125L162.5 53.188v216.75H180.063v35.093H333V269.94h17.564l-.002-216.594-95-32.22zm-143.22 9.156v239.657h31.47V30.28h-31.47zm256.907 0v239.657h31.47V30.28h-31.47zM203.687 59.157l30.938 33.875 22.188-33.28 22.218 33.28 28.595-31.53-11.688 63.656h-80l-12.25-66zM77.844 288.626v34.28h83.53v-34.28h-83.53zm273.844 0v34.28h83.53v-34.28h-83.53zm-171.625 35.093v17.874h-17.408v15.22l187.75-.002v-15.218H333V323.72H180.062zM95.25 341.593v150.47l48.72-.002V341.595H95.25zm273.844 0v150.47l48.72-.002-.002-150.468h-48.718zM162.656 375.5v97.156h187.75V375.5h-187.75z";

pub fn vis(input: &Input, out: &Output, t: usize) -> (i64, String, String) {
    let D = 600 / input.N;
    let BW = D * input.N;
    let W = 970;
    let H = 600;

    let (score, err, states) = compute_score_details(input, out);

    let t = if states.is_empty() {
        0
    } else {
        t.min(states.len() - 1)
    };

    let cur = states[t];
    let mask = cur.mask;

    let mut switch = mat![!0; input.N; input.N];
    for sw in &out.switches {
        switch[sw.i][sw.j] = sw.k;
    }

    let k_color = |k: usize| -> String { color(k as f64 / input.K as f64) };

    let mut doc = svg::Document::new()
        .set("id", "vis")
        .set("viewBox", (-5, -5, W + 10, H + 10))
        .set("width", W + 10)
        .set("height", H + 10)
        .set("style", "background-color:white");

    doc = doc.add(Style::new(
        "text {
            font-family: sans-serif;
            text-anchor: middle;
            dominant-baseline: central;
        }"
        .to_owned(),
    ));

    let mut board = Group::new();

    // cells
    for i in 0..input.N {
        for j in 0..input.N {
            let fill = if input.c[i][j] == '#' {
                "#444444"
            } else {
                "#ffffff"
            };

            let cell_title = format!(
                "cell ({}, {}) / {}{}",
                i,
                j,
                if input.c[i][j] == '#' {
                    "obstacle"
                } else {
                    "empty"
                },
                if switch[i][j] != !0 {
                    format!(" / switch {}", switch[i][j])
                } else {
                    String::new()
                }
            );

            let cell = group(cell_title).add(
                rect(j * D, i * D, D, D, fill)
                    .set("stroke", "#dddddd")
                    .set("stroke-width", 1),
            );
            board = board.add(cell);
        }
    }

    // recent path: last N actions
    let from = t.saturating_sub(input.N);
    if from < t {
        let points = (from..=t)
            .map(|u| {
                let s = states[u];
                format!("{},{}", s.j * D + D / 2, s.i * D + D / 2)
            })
            .collect::<Vec<_>>()
            .join(" ");

        board = board.add(
            Polyline::new()
                .set("points", points)
                .set("fill", "none")
                .set("stroke", "#000000")
                .set("stroke-width", 4)
                .set("stroke-opacity", 0.35)
                .set("stroke-linejoin", "round")
                .set("stroke-linecap", "round"),
        );
    }

    // switches on board
    for sw in &out.switches {
        let col = k_color(sw.k);
        let cx = sw.j * D + D / 2;
        let cy = sw.i * D + D / 2;
        let on = ((mask >> sw.k) & 1) == 1;

        let mut g = group(format!(
            "switch {} at ({}, {}) / state = {}",
            sw.k,
            sw.i,
            sw.j,
            if on { 1 } else { 0 }
        ));

        g = g.add(
            Circle::new()
                .set("cx", cx)
                .set("cy", cy)
                .set("r", D * 3 / 10)
                .set("fill", col.clone())
                .set("fill-opacity", if on { 0.95 } else { 0.35 })
                .set("stroke", "#222222")
                .set("stroke-width", 1),
        );

        g = g.add(
            Text::new(format!("{}", sw.k))
                .set("x", cx)
                .set("y", cy)
                .set("font-size", D * 2 / 5)
                .set("fill", "#000000"),
        );

        board = board.add(g);
    }

    // doors
    for door in &out.doors {
        let k = door.k / 2;
        let col = k_color(k);
        let open = is_open(door.k, mask);

        let mut line = if door.d == 0 {
            let x1 = door.j * D;
            let x2 = (door.j + 1) * D;
            let y = (door.i + 1) * D;
            Line::new()
                .set("x1", x1)
                .set("y1", y)
                .set("x2", x2)
                .set("y2", y)
        } else {
            let x = (door.j + 1) * D;
            let y1 = door.i * D;
            let y2 = (door.i + 1) * D;
            Line::new()
                .set("x1", x)
                .set("y1", y1)
                .set("x2", x)
                .set("y2", y2)
        };

        line = line
            .set("stroke", col)
            .set("stroke-width", if open { 3 } else { 6 })
            .set("stroke-linecap", "round")
            .set("stroke-opacity", if open { 0.35 } else { 1.0 });

        if open {
            line = line.set("stroke-dasharray", "5,5");
        }

        let title = if door.d == 0 {
            format!(
                "door {} / switch {} / {} / between ({}, {}) and ({}, {})",
                door.k,
                k,
                if open { "open" } else { "closed" },
                door.i,
                door.j,
                door.i + 1,
                door.j
            )
        } else {
            format!(
                "door {} / switch {} / {} / between ({}, {}) and ({}, {})",
                door.k,
                k,
                if open { "open" } else { "closed" },
                door.i,
                door.j,
                door.i,
                door.j + 1
            )
        };

        board = board.add(group(title).add(line));
    }

    // goal (throne): crown SVG
    let goal_size = D * 4 / 5;
    let gx = (input.N - 1) * D + (D - goal_size) / 2;
    let gy = (input.N - 1) * D + (D - goal_size) / 2;
    let goal_scale = goal_size as f64 / 512.0;

    let goal_group = svg::node::element::Group::new()
        .set(
            "transform",
            format!("translate({}, {}) scale({})", gx, gy, goal_scale),
        )
        .add(
            svg::node::element::Path::new()
                .set("fill", "#000000")
                .set("d", GOAL_SVG_PATH),
        );

    board = board.add(group(format!("goal at ({}, {})", input.N - 1, input.N - 1)).add(goal_group));

    // current hero position: embedded PNG
    let hero_size = D;
    let hx = cur.j as i32 * D as i32 + D as i32 / 2 - hero_size as i32 / 2;
    let hy = cur.i as i32 * D as i32 + D as i32 / 2 - hero_size as i32 / 2;

    board = board.add(
        group(format!("hero at ({}, {})", cur.i, cur.j)).add(
            svg::node::element::Image::new()
                .set("x", hx)
                .set("y", hy)
                .set("width", hero_size)
                .set("height", hero_size)
                .set("href", HERO_PNG)
                .set("preserveAspectRatio", "xMidYMid meet"),
        ),
    );

    // board frame
    board = board.add(
        Rectangle::new()
            .set("x", 0)
            .set("y", 0)
            .set("width", BW)
            .set("height", BW)
            .set("fill", "none")
            .set("stroke", "#000000")
            .set("stroke-width", 2),
    );

    doc = doc.add(board);

    // right panel: switch state history
    let rx = BW + 20;
    let ry = 0;
    let time_w = 80;
    let bit_w = 26;
    let row_h = 30;
    let panel_w = time_w + bit_w * input.K;
    let max_rows = H / row_h - 1;

    let mut panel = Group::new();

    panel = panel.add(
        rect(rx - 1, 0, panel_w + 2, H, "#fafafa")
            .set("stroke", "#bbbbbb")
            .set("stroke-width", 1),
    );

    // table header
    panel = panel.add(
        rect(rx, ry, time_w, row_h, "#eeeeee")
            .set("stroke", "#999999")
            .set("stroke-width", 1),
    );
    panel = panel.add(
        Text::new("t")
            .set("x", rx + time_w / 2)
            .set("y", ry + row_h / 2)
            .set("font-size", 15)
            .set("fill", "#000000"),
    );

    for k in 0..input.K {
        let x = rx + time_w + k * bit_w;
        let col = k_color(k);
        panel = panel.add(
            group(format!("switch {}", k)).add(
                rect(x, ry, bit_w, row_h, &col)
                    .set("stroke", "#999999")
                    .set("stroke-width", 1)
                    .set("fill-opacity", 0.65),
            ),
        );
        panel = panel.add(
            Text::new(format!("{}", k))
                .set("x", x + bit_w / 2)
                .set("y", ry + row_h / 2)
                .set("font-size", 14)
                .set("fill", "#000000"),
        );
    }

    // states at times where switch mask changed, newest first
    let changes = (1..=t)
        .filter(|&u| states[u].mask != states[u - 1].mask)
        .map(|u| (u, states[u].mask))
        .collect::<Vec<_>>();

    for (r, &(time, m)) in changes.iter().rev().take(max_rows).enumerate() {
        let y = ry + row_h * (r + 1);

        panel = panel.add(
            group(format!(
                "turn {} / mask = {:0width$b}",
                time,
                m,
                width = input.K
            ))
            .add(
                rect(rx, y, time_w, row_h, "#ffffff")
                    .set("stroke", "#cccccc")
                    .set("stroke-width", 1),
            ),
        );
        panel = panel.add(
            Text::new(format!("{}", time))
                .set("x", rx + time_w / 2)
                .set("y", y + row_h / 2)
                .set("font-size", 15)
                .set("fill", "#000000"),
        );

        for k in 0..input.K {
            let x = rx + time_w + k * bit_w;
            let col = k_color(k);
            let bit = (m >> k) & 1;

            panel = panel.add(
                group(format!("turn {} / switch {} = {}", time, k, bit)).add(
                    rect(x, y, bit_w, row_h, if bit == 1 { &col } else { "#ffffff" })
                        .set("stroke", col.clone())
                        .set("stroke-width", 1)
                        .set("fill-opacity", if bit == 1 { 0.85 } else { 1.0 }),
                ),
            );

            panel = panel.add(
                Text::new(format!("{}", bit))
                    .set("x", x + bit_w / 2)
                    .set("y", y + row_h / 2)
                    .set("font-size", 14)
                    .set("fill", "#000000"),
            );
        }
    }

    doc = doc.add(panel);

    (score, err, doc.to_string())
}
