import AIUsageCore
import Charts
import SwiftUI

// MARK: - Daily token bars

/// Daily token bars with the accepted marks (handoff §8):
/// measured (input/output stacked, or total), measured zero (2 pt baseline tick),
/// missing (dashed box, never a bar), not collected (shaded band / dotted baseline),
/// today (dashed outline over a light fill). One keyboard stop; ←/→/Home/End move a live readout.
public struct DayBarChart: View {
    public enum Style { case mini, full }

    private let provider: Provider
    private let days: [Day]
    private let style: Style
    private let today: String
    @State private var selected: Int?
    @FocusState private var focused: Bool

    /// `today`: the report's current local date; only that day is labelled "Today".
    public init(provider: Provider, days: [Day], style: Style, today: String) {
        self.provider = provider
        self.days = days
        self.style = style
        self.today = today
    }

    private var plotHeight: CGFloat { style == .mini ? 40 : 180 }
    private var gutter: CGFloat { style == .mini ? 0 : 44 }
    private var maxValue: Double {
        let peak = days.compactMap { $0.hasValue ? $0.total : nil }.max() ?? 0
        return peak > 0 ? peak * 1.08 : 1
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            GeometryReader { proxy in
                let plotWidth = proxy.size.width - gutter
                let slot = plotWidth / CGFloat(max(days.count, 1))
                ZStack(alignment: .topLeading) {
                    if style == .full { gridlines(width: plotWidth) }
                    ForEach(Array(days.enumerated()), id: \.element.id) { index, day in
                        bar(day, index: index, slot: slot)
                            .frame(width: slot, height: plotHeight, alignment: .bottom)
                            .offset(x: gutter + CGFloat(index) * slot)
                    }
                    Rectangle().fill(T.Color.lineStrong.color).frame(width: plotWidth, height: 1)
                        .offset(x: gutter, y: plotHeight)
                }
                .contentShape(Rectangle())
                .onContinuousHover { phase in
                    switch phase {
                    case let .active(point):
                        let index = Int((point.x - gutter) / max(slot, 1))
                        selected = (0..<days.count).contains(index) ? index : nil
                    case .ended:
                        if !focused { selected = nil }
                    }
                }
            }
            .frame(height: plotHeight + 1)
            axis
            Text(readout)
                .font(style == .mini ? .auCaption : .auBody)
                .foregroundStyle(T.Color.muted.color)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityHidden(true)
        }
        .focusable()
        .focused($focused)
        .focusEffectDisabled(false)
        .onKeyPress(.leftArrow) { move(-1) }
        .onKeyPress(.rightArrow) { move(1) }
        .onKeyPress(.home) { jump(0) }
        .onKeyPress(.end) { jump(days.count - 1) }
        .onChange(of: focused) { _, isFocused in
            if isFocused && selected == nil { selected = days.count - 1 }
            if !isFocused { selected = nil }
        }
        .accessibilityElement()
        .accessibilityLabel("\(provider.name) daily tokens, \(days.count) days")
        .accessibilityValue(readout)
        .accessibilityAdjustableAction { direction in
            switch direction {
            case .increment: _ = move(1)
            case .decrement: _ = move(-1)
            @unknown default: break
            }
        }
    }

    private func move(_ delta: Int) -> KeyPress.Result {
        guard !days.isEmpty else { return .ignored }
        let current = selected ?? days.count - 1
        selected = max(0, min(days.count - 1, current + delta))
        return .handled
    }

    private func jump(_ index: Int) -> KeyPress.Result {
        guard !days.isEmpty else { return .ignored }
        selected = max(0, min(days.count - 1, index))
        return .handled
    }

    private var readout: String {
        if let selected, days.indices.contains(selected) {
            return Presentation.readout(provider, day: days[selected], asOf: provider.today?.asOf, today: today)
        }
        return Presentation.chartCaption(provider, days: days)
    }

    @ViewBuilder
    private func gridlines(width: CGFloat) -> some View {
        ForEach([1.0, 0.5, 0.0], id: \.self) { fraction in
            let y = plotHeight - CGFloat(fraction / 1.08) * plotHeight
            ZStack(alignment: .leading) {
                Rectangle().fill(T.Color.line.color).frame(width: width, height: 1).offset(x: gutter)
                Text(fraction == 0 ? "0" : Format.tokens(maxValue / 1.08 * fraction))
                    .font(.auChart).foregroundStyle(T.Color.muted.color)
                    .frame(width: gutter - 6, alignment: .trailing)
            }
            .offset(y: y - 7)
        }
    }

    @ViewBuilder
    private func bar(_ day: Day, index: Int, slot: CGFloat) -> some View {
        let width = max(3, slot * (style == .mini ? 0.62 : 0.7))
        let isSelected = selected == index
        let scale = plotHeight / CGFloat(maxValue)
        VStack(spacing: 0) {
            Spacer(minLength: 0)
            switch day.state {
            case .measured:
                stack(day, width: width, scale: scale)
            case .zero:
                Rectangle().fill(T.Color.text.color).frame(width: width, height: 2)
            case .missing:
                RoundedRectangle(cornerRadius: 2)
                    .strokeBorder(T.Color.muted.color, style: StrokeStyle(lineWidth: 1, dash: [3, 2]))
                    .frame(width: width, height: plotHeight * 0.38)
            case .notCollected:
                if style == .mini {
                    Rectangle().fill(T.Color.muted.color.opacity(0.6))
                        .frame(width: width, height: 1)
                        .mask(HStack(spacing: 2) { ForEach(0..<12, id: \.self) { _ in Rectangle().frame(width: 1) } })
                } else {
                    Rectangle().fill(T.Color.chartNotCollected.color.opacity(0.55)).frame(width: slot, height: plotHeight)
                }
            case .partial:
                let height = max(6, CGFloat(day.total ?? 0) * scale)
                RoundedRectangle(cornerRadius: 2)
                    .fill(T.Color.chartToday.color)
                    .overlay(RoundedRectangle(cornerRadius: 2)
                        .strokeBorder(T.Color.accent.color, style: StrokeStyle(lineWidth: 1.2, dash: [3, 2])))
                    .frame(width: width, height: height)
            }
        }
        .frame(maxWidth: .infinity)
        .background(isSelected ? T.Color.hover.color : .clear, in: RoundedRectangle(cornerRadius: 3))
    }

    @ViewBuilder
    private func stack(_ day: Day, width: CGFloat, scale: CGFloat) -> some View {
        if provider.usage.hasSplit, let input = day.input, let output = day.output {
            VStack(spacing: 0) {
                Rectangle().fill(T.Color.chartOutput.color).frame(height: CGFloat(output) * scale)
                Rectangle().fill(T.Color.chartInput.color).frame(height: CGFloat(input) * scale)
            }
            .frame(width: width)
            .clipShape(UnevenRoundedRectangle(topLeadingRadius: 2, topTrailingRadius: 2))
        } else {
            UnevenRoundedRectangle(topLeadingRadius: 2, topTrailingRadius: 2)
                .fill(T.Color.chartInput.color)
                .frame(width: width, height: max(1, CGFloat(day.total ?? 0) * scale))
        }
    }

    private var axis: some View {
        HStack {
            if let first = days.first { Text(Format.dayLabel(first.date)) }
            Spacer()
            if style == .full, days.count > 2 {
                Text(Format.dayLabel(days[days.count / 2].date))
                Spacer()
            }
            if let last = days.last {
                let isToday = last.date == today
                Text(isToday ? (style == .mini ? "Today" : "\(Format.dayLabel(last.date)) · today") : Format.dayLabel(last.date))
                    .fontWeight(isToday ? .semibold : .regular)
            }
        }
        .font(.auChart)
        .foregroundStyle(T.Color.muted.color)
        .padding(.leading, gutter)
        .accessibilityHidden(true)
    }
}

/// Legend for full token charts.
public struct DayChartLegend: View {
    private let split: Bool
    public init(split: Bool) { self.split = split }

    public var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 104), spacing: T.Space.s2, alignment: .leading)],
                  alignment: .leading, spacing: 4) {
            if split {
                swatch(T.Color.chartInput.color, "Input")
                swatch(T.Color.chartOutput.color, "Output")
            } else {
                swatch(T.Color.chartInput.color, "Total (split not reported)")
            }
            dashed("Today so far", T.Color.accent.color)
            dashed("Missing", T.Color.muted.color)
            HStack(spacing: 4) { Rectangle().fill(T.Color.text.color).frame(width: 10, height: 2); Text("Measured zero").fixedSize() }
            swatch(T.Color.chartNotCollected.color, "Not collected")
        }
        .font(.auChart)
        .foregroundStyle(T.Color.muted.color)
        .accessibilityElement(children: .combine)
    }

    private func swatch(_ color: Color, _ text: String) -> some View {
        HStack(spacing: 4) { RoundedRectangle(cornerRadius: 2).fill(color).frame(width: 9, height: 9); Text(text).fixedSize() }
    }

    private func dashed(_ text: String, _ color: Color) -> some View {
        HStack(spacing: 4) {
            RoundedRectangle(cornerRadius: 2).strokeBorder(color, style: StrokeStyle(lineWidth: 1, dash: [2, 2]))
                .frame(width: 9, height: 9)
            Text(text).fixedSize()
        }
    }
}

// MARK: - Quota line

/// Quota percentage over a range. The line breaks at resets and at gaps; resets
/// are dashed rules; a stale window is extended to now as a dashed amber line.
public struct QuotaLineChart: View {
    private let window: QuotaWindow
    private let rangeSeconds: TimeInterval
    private let now: Date
    private let gapSeconds: TimeInterval
    @State private var selected: Int?
    @FocusState private var focused: Bool

    public init(window: QuotaWindow, rangeSeconds: TimeInterval, now: Date, gapSeconds: TimeInterval) {
        self.window = window
        self.rangeSeconds = rangeSeconds
        self.now = now
        self.gapSeconds = gapSeconds
    }

    private struct Point: Identifiable {
        var id: Int
        var t: Date
        var percent: Double
        var segment: Int
    }

    private var start: Date { now.addingTimeInterval(-rangeSeconds) }

    private var points: [Point] {
        let readings = window.readings.filter { $0.t >= start && $0.t <= now }
        var segment = 0
        var out: [Point] = []
        for (index, reading) in readings.enumerated() {
            if let previous = out.last {
                let crossedReset = window.resets.contains { $0 > previous.t && $0 <= reading.t }
                if crossedReset || reading.t.timeIntervalSince(previous.t) > gapSeconds { segment += 1 }
            }
            out.append(Point(id: index, t: reading.t, percent: reading.percent, segment: segment))
        }
        return out
    }

    private var resets: [Date] { window.resets.filter { $0 >= start && $0 <= now } }

    public var body: some View {
        let points = self.points
        VStack(alignment: .leading, spacing: 4) {
            Chart {
                ForEach(resets, id: \.self) { reset in
                    RuleMark(x: .value("Reset", reset))
                        .foregroundStyle(T.Color.muted.color)
                        .lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 3]))
                        .annotation(position: .top, alignment: .leading) {
                            if reset == resets.last { Text("reset").font(.auChart).foregroundStyle(T.Color.muted.color) }
                        }
                }
                ForEach(points) { point in
                    LineMark(x: .value("Time", point.t), y: .value("Percent", point.percent),
                             series: .value("Segment", point.segment))
                        .foregroundStyle(T.Color.accent.color)
                        .interpolationMethod(.linear)
                }
                if let last = points.last {
                    PointMark(x: .value("Time", last.t), y: .value("Percent", last.percent))
                        .foregroundStyle(T.Color.accent.color).symbolSize(24)
                    if window.status == .stale {
                        LineMark(x: .value("Time", last.t), y: .value("Percent", last.percent), series: .value("Segment", -1))
                            .foregroundStyle(T.Color.chartStale.color)
                            .lineStyle(StrokeStyle(lineWidth: 1.5, dash: [4, 3]))
                        LineMark(x: .value("Time", now), y: .value("Percent", last.percent), series: .value("Segment", -1))
                            .foregroundStyle(T.Color.chartStale.color)
                            .lineStyle(StrokeStyle(lineWidth: 1.5, dash: [4, 3]))
                    }
                }
                if let selected, points.indices.contains(selected) {
                    RuleMark(x: .value("Selected", points[selected].t)).foregroundStyle(T.Color.focus.color.opacity(0.5))
                }
            }
            .chartXScale(domain: start...now)
            .chartYScale(domain: 0...100)
            .chartYAxis {
                AxisMarks(position: .leading, values: [0, 50, 100]) { value in
                    AxisGridLine().foregroundStyle(T.Color.line.color)
                    AxisValueLabel { Text("\(value.as(Int.self) ?? 0)%").font(.auChart) }
                }
            }
            .chartXAxis {
                AxisMarks(values: [start, start.addingTimeInterval(rangeSeconds / 2), now]) { value in
                    AxisValueLabel {
                        if let date = value.as(Date.self) {
                            Text(date == now ? "now" : axisLabel(date)).font(.auChart)
                        }
                    }
                }
            }
            .frame(height: 150)
            Text(readout(points))
                .font(.auCaption)
                .foregroundStyle(T.Color.muted.color)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityHidden(true)
        }
        .focusable()
        .focused($focused)
        .onKeyPress(.leftArrow) { step(-1, points.count) }
        .onKeyPress(.rightArrow) { step(1, points.count) }
        .onKeyPress(.home) { selected = points.isEmpty ? nil : 0; return .handled }
        .onKeyPress(.end) { selected = points.isEmpty ? nil : points.count - 1; return .handled }
        .onChange(of: focused) { _, isFocused in selected = isFocused ? (points.isEmpty ? nil : points.count - 1) : nil }
        .accessibilityElement()
        .accessibilityLabel("\(window.label), % \(window.basis == .remaining ? "remaining" : "used")")
        .accessibilityValue(readout(points))
    }

    private func step(_ delta: Int, _ count: Int) -> KeyPress.Result {
        guard count > 0 else { return .ignored }
        selected = max(0, min(count - 1, (selected ?? count - 1) + delta))
        return .handled
    }

    private func axisLabel(_ date: Date) -> String {
        rangeSeconds <= 86400 ? Format.clock(date) : Format.moment(date, now: now)
    }

    private func readout(_ points: [Point]) -> String {
        let basis = window.basis == .remaining ? "remaining" : "used"
        if let selected, points.indices.contains(selected) {
            let point = points[selected]
            var text = "\(Format.moment(point.t, now: now)): \(Format.percent(point.percent)) \(basis)"
            if selected > 0, points[selected - 1].segment != point.segment {
                let afterReset = window.resets.contains { $0 > points[selected - 1].t && $0 <= point.t }
                text += afterReset ? " · after a reset" : " · after a gap in readings"
            }
            return text
        }
        guard let last = points.last else { return "No readings in this range." }
        var text = "\(window.label), % \(basis) · last reading \(Format.moment(last.t, now: now)): \(Format.percent(last.percent)) \(basis)"
        if !resets.isEmpty { text += " · \(resets.count) reset\(resets.count == 1 ? "" : "s") in range" }
        if window.status == .stale { text += " · stale, dashed to now" }
        return text
    }
}
