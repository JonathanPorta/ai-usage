import AIUsageCore
import AIUsageDesign
import SwiftUI

/// History's All tools page (phase 4): one section per tool with an account tracked now,
/// each with its own figures, its accounts stacked per day, and every account's breakdown.
/// Everything shown comes from `AllToolsPage`; tools are never added to each other.
struct AllToolsView: View {
    let report: Report
    @Binding var range: Int
    let compact: Bool
    let now: Date
    let open: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: T.Space.s4) {
            HStack(alignment: .center, spacing: T.Space.s3) {
                Text("All tools").font(.auTitle).lineLimit(1).accessibilityAddTraits(.isHeader)
                Spacer(minLength: T.Space.s2)
                if let last = report.collection.lastAttemptAt {
                    Text("Last check \(Format.moment(last, now: now))").font(.auCaption)
                        .foregroundStyle(T.Color.muted.color).lineLimit(1)
                }
            }
            .frame(height: 30)
            ControlRow(compact: compact, title: "Daily tokens by tool · \(range) days",
                       subtitle: "Each tool's accounts stacked, to \(Format.longDay(report.today))") {
                Segmented("Range", selection: $range, options: [(7, "7 days"), (30, "30 days"), (90, "90 days")])
            }
            if let page = AllToolsPage(report, range: range) {
                ForEach(page.sections) { section in
                    ToolSection(section: section, today: report.today, compact: compact, open: open)
                }
                if let notIncluded = page.notIncluded {
                    Text(notIncluded).font(.auCaption).foregroundStyle(T.Color.muted.color)
                }
                AboutNumbers(text: AllToolsPage.about)
            }
        }
    }
}

private struct ToolSection: View {
    let section: AllToolsSection
    let today: String
    let compact: Bool
    let open: (String) -> Void

    var body: some View {
        Panel {
            VStack(alignment: .leading, spacing: T.Space.s3) {
                Text(section.title).font(.auLead).lineLimit(1).accessibilityAddTraits(.isHeader)
                HStack(alignment: .firstTextBaseline, spacing: T.Space.s5) {
                    figure(section.range, section.rangeCaption)
                    figure(section.today, section.todayCaption)
                    Spacer(minLength: 0)
                }
                if section.hasReadings {
                    GroupDayChart(section: section, today: today)
                    GroupChartLegend(series: section.series)
                }
                Breakdown(rows: section.rows, range: section.bars.count, showsSwatch: section.rows.count > 1,
                          open: open)
                if let excluded = section.excluded {
                    Text(excluded).font(.auCaption).foregroundStyle(T.Color.muted.color)
                }
            }
        }
    }

    private func figure(_ figure: AllToolsFigure, _ caption: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(figure.text).font(.system(size: 17, weight: .medium).monospacedDigit())
                .foregroundStyle(T.Color.text.color).lineLimit(1)
            Text(caption).font(.auCaption).foregroundStyle(T.Color.muted.color).lineLimit(1)
        }
        .accessibilityElement(children: .combine)
    }
}

/// Every account tracked now, including those drawn together as "Other accounts":
/// its own figures and status, and its name opens its own History page.
private struct Breakdown: View {
    let rows: [AllToolsRow]
    let range: Int
    let showsSwatch: Bool
    let open: (String) -> Void

    var body: some View {
        VStack(spacing: 0) {
            line(header: true, swatch: nil, name: Text("Account"), total: "Total · \(range) days",
                 today: "Today", status: "Status")
            ForEach(rows) { row in
                Divider()
                line(header: false, swatch: showsSwatch ? row.series : nil,
                     name: Button { open(row.id) } label: { Text(row.name).underline() }
                         .buttonStyle(.plain)
                         .foregroundStyle(T.Color.accent.color)
                         .accessibilityHint("Opens \(row.name)'s History"),
                     total: row.range.text, today: row.today.text, status: row.status)
            }
        }
        .padding(.top, T.Space.s1)
    }

    private func line<Name: View>(header: Bool, swatch: Int?, name: Name, total: String, today: String,
                                  status: String) -> some View {
        HStack(spacing: T.Space.s2) {
            HStack(spacing: 6) {
                if let swatch {
                    RoundedRectangle(cornerRadius: 2).fill(GroupDayChart.color(swatch)).frame(width: 9, height: 9)
                        .accessibilityHidden(true)
                }
                // Long names wrap; nothing in the breakdown is truncated.
                name.fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Text(total).frame(width: 110, alignment: .trailing)
            Text(today).frame(width: 90, alignment: .trailing)
            Text(status).frame(maxWidth: .infinity, alignment: .leading).fixedSize(horizontal: false, vertical: true)
        }
        .font(header ? .auCaption.weight(.semibold) : .auCaption.monospacedDigit())
        .foregroundStyle(header ? T.Color.muted.color : T.Color.text.color)
        .padding(.vertical, 5)
        .accessibilityElement(children: .combine)
    }
}
