// jtag_bridge.sv  -  Virtual JTAG <-> sys_clk register bridge and data buffer
//
// Virtual IR (2 bits) selects the data register:
//   0  REG        40-bit register-file command frame (below)
//   1  BUF_STORE  buffer write stream
//   2  BUF_LOAD   buffer read stream
//
// Register map (16 x 32-bit):
//   0..7   JWSR  JTAG writes, system reads. Stored in the sys_clk domain.
//   8..15  SWJR  System writes, JTAG reads. Sampled from the swjr input.
//
// DR frame (40 bits, LSB shifted first):
//   request : [3:0] addr, [4] wr, [36:5] data, [38:37] reserved, [39] nop
//   response: [36:5] rd_data, [37] done, [38] overrun, [39] 0
//
// TCK only runs during scans, so every command crosses into sys_clk with a
// req/ack toggle handshake: Update-DR latches the command into cmd_hold and
// toggles req; sys_clk executes it and returns ack = req. cmd_hold and rd_hold
// are only sampled after the toggle has passed a 2-FF synchronizer, so the
// multi-bit fields are stable when read. A command arriving while the previous
// one is still in flight is dropped and reported through the overrun flag.
//
// Buffer (2**BUF_ADDR_BITS x 32-bit dual-port RAM, default 256 words = 1 KiB,
// top.sv uses 4096 words = 16 KiB;
// port A on TCK, port B on sys_clk):
// BUF_STORE and BUF_LOAD behave as one DR of up to 32 * 2**BUF_ADDR_BITS bits,
// word 0 bit 0 shifted first, held in a single 32-bit shift register. Every
// scan starts at word 0, and a scan of n * 32 bits transfers words 0..n-1
// (the address wraps after the last word).
//   STORE: each 32nd shift writes the completed word to RAM and steps the
//          address. A trailing partial word is discarded.
//   LOAD:  Capture-DR loads word 0, and each 32nd shift reloads the register
//          with the next word, which the RAM has already prefetched. TDI is
//          ignored.
// The two ports are not arbitrated: the system must leave the buffer alone
// while JTAG scans it, coordinated through the registers. In top.sv the
// system port belongs to sys_access.sv, which only uses it while a command
// is in flight.
module jtag_bridge #(
    parameter int BUF_ADDR_BITS = 8         // buffer depth 2**BUF_ADDR_BITS words
) (
    input  wire              sys_clk,
    input  wire              sys_rst_n,
    output logic [7:0][31:0] jwsr,
    output logic [7:0]       jwsr_wr,   // one sys_clk pulse per JTAG write
    input  wire  [7:0][31:0] swjr,

    // Buffer system port: en && rd reads (buf_rdata valid next cycle),
    // en && !rd writes buf_wdata.
    input  wire              buf_en,
    input  wire              buf_rd,
    input  wire  [BUF_ADDR_BITS-1:0] buf_addr,
    input  wire  [31:0]      buf_wdata,
    output wire  [31:0]      buf_rdata
);
    localparam logic [1:0] IR_REG       = 2'd0;
    localparam logic [1:0] IR_BUF_STORE = 2'd1;
    localparam logic [1:0] IR_BUF_LOAD  = 2'd2;

    wire jtag_tck;
    wire jtag_tdi;
    wire jtag_tdo;
    wire jtag_state_tlr;
    wire [1:0] virtual_ir_in;
    wire virtual_state_cdr;
    wire virtual_state_sdr;
    wire virtual_state_udr;

    sld_virtual_jtag #(
        .sld_auto_instance_index("YES"),
        .sld_instance_index(0),
        .sld_ir_width(2)
    ) virtual_jtag (
        .tck(jtag_tck),
        .tdi(jtag_tdi),
        .tdo(jtag_tdo),
        .ir_out(2'b0),
        .ir_in(virtual_ir_in),
        .virtual_state_cdr(virtual_state_cdr),
        .virtual_state_sdr(virtual_state_sdr),
        .virtual_state_udr(virtual_state_udr),
        .jtag_state_tlr(jtag_state_tlr)
    );

    // Handshake toggles are never reset, so a reset on either side cannot
    // leave req and ack permanently unequal. They start at 0 on configuration.
    reg        req = 1'b0;              // TCK domain
    reg        ack = 1'b0;              // sys_clk domain
    reg [36:0] cmd_hold;                // TCK domain: {data, wr, addr}
    reg [31:0] rd_hold;                 // sys_clk domain

    // ---------------------------------------------------------------- TCK side
    (* altera_attribute = "-name SYNCHRONIZER_IDENTIFICATION FORCED_IF_ASYNCHRONOUS" *)
    reg [1:0] ack_sync = 2'b0;
    reg [39:0] dr_shift;
    reg        overrun;

    wire dr_selected = virtual_ir_in == IR_REG;
    wire busy = req != ack_sync[1];
    wire issue = dr_selected && virtual_state_udr && !dr_shift[39];

    always @(posedge jtag_tck)
        ack_sync <= {ack_sync[0], ack};

    always @(posedge jtag_tck) begin
        if (issue && !busy) begin
            cmd_hold <= dr_shift[36:0];
            req <= ~req;
        end
    end

    always @(posedge jtag_tck or posedge jtag_state_tlr) begin
        if (jtag_state_tlr) begin
            dr_shift <= 40'b0;
            overrun <= 1'b0;
        end else if (dr_selected && virtual_state_cdr) begin
            dr_shift <= {1'b0, overrun, ~busy, rd_hold, 5'b0};
            overrun <= 1'b0;
        end else if (dr_selected && virtual_state_sdr) begin
            dr_shift <= {jtag_tdi, dr_shift[39:1]};
        end else if (issue && busy) begin
            overrun <= 1'b1;
        end
    end

    // ------------------------------------------------------- buffer, TCK side
    // Outside a buffer scan the RAM address rests at 0, so word 0 is already
    // on buf_q when Capture-DR loads the shift register. During the scan the
    // address is buf_word: the word being assembled (STORE) or the next word
    // to send (LOAD), whose read completes long before its 32nd shift.
    wire        buf_store = virtual_ir_in == IR_BUF_STORE;
    wire        buf_load = virtual_ir_in == IR_BUF_LOAD;
    wire        buf_selected = buf_store || buf_load;
    reg  [31:0] buf_shift;
    reg  [4:0]  buf_bit;
    reg  [BUF_ADDR_BITS-1:0] buf_word;
    reg         buf_scan;
    wire [31:0] buf_q;

    wire        buf_shifting = buf_selected && virtual_state_sdr;
    wire        buf_word_done = buf_shifting && buf_bit == 5'd31;
    wire [31:0] buf_shift_in = {jtag_tdi, buf_shift[31:1]};

    always @(posedge jtag_tck or posedge jtag_state_tlr) begin
        if (jtag_state_tlr) begin
            buf_shift <= 32'b0;
            buf_bit <= 5'd0;
            buf_word <= '0;
            buf_scan <= 1'b0;
        end else if (buf_selected && virtual_state_cdr) begin
            buf_shift <= buf_q;
            buf_bit <= 5'd0;
            buf_word <= BUF_ADDR_BITS'(buf_load);
            buf_scan <= 1'b1;
        end else if (buf_shifting) begin
            buf_shift <= buf_word_done && buf_load ? buf_q : buf_shift_in;
            buf_bit <= buf_bit + 5'd1;
            if (buf_word_done)
                buf_word <= buf_word + 1'b1;
        end else if (virtual_state_udr) begin
            buf_scan <= 1'b0;
        end
    end

    assign jtag_tdo = dr_selected ? dr_shift[0] : buf_shift[0];

    buffer_ram #(
        .ADDR_BITS(BUF_ADDR_BITS),
        .DATA_BITS(32)
    ) buffer (
        .clk_a(jtag_tck),
        .en_a(1'b1),
        .we_a(buf_store && buf_word_done),
        .addr_a(buf_scan ? buf_word : '0),
        .d_a(buf_shift_in),
        .q_a(buf_q),

        .clk_b(sys_clk),
        .en_b(buf_en),
        .we_b(!buf_rd),
        .addr_b(buf_addr),
        .d_b(buf_wdata),
        .q_b(buf_rdata)
    );

    // ------------------------------------------------------------ sys_clk side
    (* altera_attribute = "-name SYNCHRONIZER_IDENTIFICATION FORCED_IF_ASYNCHRONOUS" *)
    reg [1:0] req_sync = 2'b0;

    wire [3:0]  cmd_addr = cmd_hold[3:0];
    wire        cmd_wr   = cmd_hold[4];
    wire [31:0] cmd_data = cmd_hold[36:5];

    always @(posedge sys_clk)
        req_sync <= {req_sync[0], req};

    // While in reset, pending commands wait (the host sees done = 0) and run
    // after release. JTAG writes to SWJR addresses are ignored.
    always @(posedge sys_clk) begin
        jwsr_wr <= 8'b0;
        if (!sys_rst_n) begin
            jwsr <= '0;
            rd_hold <= 32'h0;
        end else if (req_sync[1] != ack) begin
            ack <= req_sync[1];
            if (cmd_wr) begin
                if (!cmd_addr[3]) begin
                    jwsr[cmd_addr[2:0]] <= cmd_data;
                    jwsr_wr[cmd_addr[2:0]] <= 1'b1;
                end
            end else begin
                rd_hold <= cmd_addr[3] ? swjr[cmd_addr[2:0]] : jwsr[cmd_addr[2:0]];
            end
        end
    end
endmodule
