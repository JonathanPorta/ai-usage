import AIUsageCore
import SwiftUI

// MARK: - All tools: one tool's accounts stacked per day

/// One tool's daily tokens with its accounts stacked (phase 4). The marks follow the day
/// chart: zero tick, missing box, not-collected band, dashed outline for a day so far.
/// A bar is exactly as tall as its known total. An incomplete day (an account without a
/// reading) gets a marker in the strip below the baseline, which adds no height.
/// One keyboard stop; ←/→/Home/End move a live readout naming every account.
public struct GroupDayChart: View {
    private let section: AllToolsSection
    private let today: String
    @State private var selected: Int?
    @FocusState private var focused: Bool
    /// Offscreen renders only: the day shown selected, so its readout can be reviewed.
    @Environment(\.chartPreviewDate) private var previewDate

    private let plotHeight: CGFloat = 160
    private let gutter: CGFloat = 44
    private let strip: CGFloat = 12

    public init(section: AllToolsSection, today: String) {
        self.section = section
        self.today = today
    }

    private var bars: [AllToolsBar] { section.bars }

    public static func color(_ series: Int) -> Color {
        switch series {
        case 0: return T.Color.chartAccount1.color
        case 1: return T.Color.chartAccount2.color
        case 2: return T.Color.chartAccount3.color
        default: return T.Color.chartAccount4.color
        }
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            GeometryReader { proxy in
                let plotWidth = proxy.size.width - gutter
                let slot = plotWidth / CGFloat(max(bars.count, 1))
                let scale = AllToolsBar.scale(bars, plotHeight: Double(plotHeight))
                ZStack(alignment: .topLeading) {
                    gridlines(width: plotWidth)
                    ForEach(Array(bars.enumerated()), id: \.element.date) { index, bar in
                        self.bar(bar, index: index, slot: slot, scale: scale)
                            .frame(width: slot, height: plotHeight, alignment: .bottom)
                            .offset(x: gutter + CGFloat(index) * slot)
                        if bar.marker {
                            Image(systemName: "circle.dashed")
                                .font(.system(size: 9, weight: .semibold))
                                .foregroundStyle(T.Color.muted.color)
                                .frame(width: slot, height: strip)
                                .offset(x: gutter + CGFloat(index) * slot, y: plotHeight + 2)
                        }
                    }
                    Rectangle().fill(T.Color.lineStrong.color).frame(width: plotWidth, height: 1)
                        .offset(x: gutter, y: plotHeight)
                }
                .contentShape(Rectangle())
                .onContinuousHover { phase in
                    switch phase {
                    case let .active(point):
                        let index = Int((point.x - gutter) / max(slot, 1))
                        selected = bars.indices.contains(index) ? index : nil
                    case .ended:
                        if !focused { selected = nil }
                    }
                }
            }
            .frame(height: plotHeight + 1 + strip + 2)
            axis
            // Sized to the tallest text it can show, so moving between days never
            // changes the chart's height.
            ZStack(alignment: .topLeading) {
                ForEach(Array(readouts.enumerated()), id: \.offset) { _, text in caption(text).hidden() }
                caption(readout)
            }
            .accessibilityHidden(true)
        }
        .focusable()
        .focused($focused)
        .focusEffectDisabled(false)
        .onKeyPress(.leftArrow) { move(-1) }
        .onKeyPress(.rightArrow) { move(1) }
        .onKeyPress(.home) { jump(0) }
        .onKeyPress(.end) { jump(bars.count - 1) }
        .onChange(of: focused) { _, isFocused in
            if isFocused && selected == nil { selected = bars.count - 1 }
            if !isFocused { selected = nil }
        }
        .onAppear {
            if let previewDate { selected = bars.firstIndex { $0.date == previewDate } }
        }
        .accessibilityElement()
        .accessibilityLabel("\(section.name) daily tokens, accounts stacked, \(bars.count) days")
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
        guard !bars.isEmpty else { return .ignored }
        selected = max(0, min(bars.count - 1, (selected ?? bars.count - 1) + delta))
        return .handled
    }

    private func jump(_ index: Int) -> KeyPress.Result {
        guard !bars.isEmpty else { return .ignored }
        selected = max(0, min(bars.count - 1, index))
        return .handled
    }

    private var readout: String {
        if let selected, bars.indices.contains(selected) { return bars[selected].readout }
        return section.caption
    }

    private var readouts: [String] { [section.caption] + bars.map(\.readout) }

    private func caption(_ text: String) -> some View {
        Text(text)
            .font(.auBody)
            .foregroundStyle(T.Color.muted.color)
            .lineLimit(3)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder
    private func gridlines(width: CGFloat) -> some View {
        let labels = Format.dayChartAxisLabels(peak: bars.compactMap(\.total).max() ?? 0)
        ForEach(Array([1.0, 0.5, 0.0].enumerated()), id: \.offset) { index, fraction in
            let y = plotHeight - CGFloat(fraction / 1.08) * plotHeight
            ZStack(alignment: .leading) {
                Rectangle().fill(T.Color.line.color).frame(width: width, height: 1).offset(x: gutter)
                Text(labels[index])
                    .font(.auChart).foregroundStyle(T.Color.muted.color)
                    .frame(width: gutter - 6, alignment: .trailing)
            }
            .offset(y: y - 7)
        }
    }

    @ViewBuilder
    private func bar(_ bar: AllToolsBar, index: Int, slot: CGFloat, scale: Double) -> some View {
        let width = max(3, slot * 0.7)
        VStack(spacing: 0) {
            Spacer(minLength: 0)
            switch bar.state {
            case .measured, .incomplete:
                stack(bar, width: width, scale: scale)
            case .partial:
                // So far: the stack in a lighter fill inside a dashed outline, as the day
                // chart draws today, in the text color so it shows over every series.
                stack(bar, width: width, scale: scale)
                    .opacity(0.45)
                    .overlay(UnevenRoundedRectangle(topLeadingRadius: 2, topTrailingRadius: 2)
                        .strokeBorder(T.Color.text.color, style: StrokeStyle(lineWidth: 1.2, dash: [3, 2])))
            case .zero:
                Rectangle().fill(T.Color.text.color).frame(width: width, height: 2)
            case .missing:
                RoundedRectangle(cornerRadius: 2)
                    .strokeBorder(T.Color.muted.color, style: StrokeStyle(lineWidth: 1, dash: [3, 2]))
                    .frame(width: width, height: plotHeight * 0.38)
            case .notCollected:
                Rectangle().fill(T.Color.chartNotCollected.color.opacity(0.55)).frame(width: slot, height: plotHeight)
            }
        }
        .frame(maxWidth: .infinity)
        .background(selected == index ? T.Color.hover.color : .clear, in: RoundedRectangle(cornerRadius: 3))
    }

    /// The known stack, bottom to top. Each boundary is a 1 pt gap cut out of the upper
    /// segment, so the stack keeps exactly the height of its known total.
    @ViewBuilder
    private func stack(_ bar: AllToolsBar, width: CGFloat, scale: Double) -> some View {
        let heights = bar.heights(scale: scale)
        if heights.isEmpty {
            // Known zeros beside a missing account: the zero tick.
            Rectangle().fill(T.Color.text.color).frame(width: width, height: 2)
        } else {
            VStack(spacing: 0) {
                ForEach(Array(bar.segments.enumerated().reversed()), id: \.offset) { position, segment in
                    let height = CGFloat(heights[position])
                    // The gap sits at the top of every segment but the highest one.
                    let gap: CGFloat = position < bar.segments.count - 1 && height > 2 ? 1 : 0
                    Rectangle().fill(Self.color(segment.series))
                        .padding(.top, gap)
                        .frame(height: max(height, position == bar.segments.count - 1 && heights.reduce(0, +) < 1 ? 1 : 0))
                }
            }
            .frame(width: width)
            .clipShape(UnevenRoundedRectangle(topLeadingRadius: 2, topTrailingRadius: 2))
        }
    }

    private var axis: some View {
        HStack {
            if let first = bars.first { Text(Format.dayLabel(first.date)) }
            Spacer()
            if bars.count > 2 {
                Text(Format.dayLabel(bars[bars.count / 2].date))
                Spacer()
            }
            if let last = bars.last {
                let isToday = last.date == today
                Text(isToday ? "\(Format.dayLabel(last.date)) · today" : Format.dayLabel(last.date))
                    .fontWeight(isToday ? .semibold : .regular)
            }
        }
        .font(.auChart)
        .foregroundStyle(T.Color.muted.color)
        .padding(.leading, gutter)
        .accessibilityHidden(true)
    }
}

/// The All tools legend: each series by name, with a small stacked swatch that fills only
/// its own position, so accounts are told apart without relying on color, then the marks.
public struct GroupChartLegend: View {
    private let series: [AllToolsSeries]
    public init(series: [AllToolsSeries]) { self.series = series }

    public var body: some View {
        FlowLayout(spacing: T.Space.s3, lineSpacing: 4) {
            ForEach(series, id: \.index) { item in
                HStack(spacing: 4) {
                    VStack(spacing: 1) {
                        ForEach((0..<max(series.count, 1)).reversed(), id: \.self) { position in
                            RoundedRectangle(cornerRadius: 1)
                                .fill(position == item.index ? GroupDayChart.color(item.index) : T.Color.lineStrong.color)
                                .frame(height: series.count > 1 ? 2 : 9)
                        }
                    }
                    .frame(width: 9, height: 10)
                    Text(item.label).fixedSize()
                }
            }
            dashed("So far (day not over)", T.Color.text.color)
            dashed("Missing", T.Color.muted.color)
            HStack(spacing: 4) {
                Image(systemName: "circle.dashed").font(.system(size: 9, weight: .semibold))
                Text("An account has no reading").fixedSize()
            }
            HStack(spacing: 4) { Rectangle().fill(T.Color.text.color).frame(width: 10, height: 2); Text("Measured zero").fixedSize() }
            HStack(spacing: 4) {
                RoundedRectangle(cornerRadius: 2).fill(T.Color.chartNotCollected.color).frame(width: 9, height: 9)
                Text("Not collected").fixedSize()
            }
        }
        .font(.auChart)
        .foregroundStyle(T.Color.muted.color)
        .accessibilityElement(children: .combine)
    }

    private func dashed(_ text: String, _ color: Color) -> some View {
        HStack(spacing: 4) {
            RoundedRectangle(cornerRadius: 2).strokeBorder(color, style: StrokeStyle(lineWidth: 1, dash: [2, 2]))
                .frame(width: 9, height: 9)
            Text(text).fixedSize()
        }
    }
}

private struct ChartPreviewDateKey: EnvironmentKey {
    static let defaultValue: String? = nil
}

public extension EnvironmentValues {
    /// Offscreen renders only: the date a chart shows selected. Nil in the app.
    var chartPreviewDate: String? {
        get { self[ChartPreviewDateKey.self] }
        set { self[ChartPreviewDateKey.self] = newValue }
    }
}
