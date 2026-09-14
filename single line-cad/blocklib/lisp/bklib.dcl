// bklib.dcl -- 块库选块对话框（配合 bklib.lsp 的 C:BKINS）

bklib_pick : dialog {
    label = "块库 - 选择要插入的块";

    : row {
        : list_box {
            key = "blist";
            width = 40;
            height = 20;
            multiple_select = false;
        }
    }

    spacer;

    : row {
        : button {
            label = "插入 (Insert)";
            key = "accept";
            is_default = true;
        }
        : button {
            label = "取消 (Cancel)";
            key = "cancel";
            is_cancel = true;
        }
    }
}
