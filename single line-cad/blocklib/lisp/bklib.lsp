;;; =========================================================================
;;;  bklib.lsp  --  块库快速插入 / 更新工具（AutoLISP）
;;;  Barnett Solar & BESS 单线图块库（半自动 L1）
;;;
;;;  功能
;;;    C:BKINS    快速插入块（对话框选块 -> 插入点 -> 自动编号 TAG -> 填充属性）
;;;    C:BKLIST   列出库中所有块
;;;    C:BKREF    一键从库重定义所有块（库 .dwg 更新后传播到当前图）
;;;    C:BKPATH   设置块库路径（会写入 bklib_path.txt）
;;;
;;;  依赖
;;;    blocklib/_manifest.csv         块注册表（"数据库"）
;;;    blocklib/blocks/<名称>.dwg     块文件（缺省回退 .dxf）
;;;    blocklib/lisp/bklib.dcl        选块对话框
;;;
;;;  加载
;;;    (load "bklib.lsp")   或   APPLOAD，或放入启动组 autoload
;;; =========================================================================

(vl-load-com)

;;; 块库默认路径（用 C:BKPATH 可改）
(setq *BKLIB-DIR* "C:/Users/ZhaokeShi/Downloads/single line-cad/blocklib")
(setq *BKLIB-ROWS* nil)   ; 缓存的注册表行
(setq *BKLIB-NAMES* nil)  ; 对话框用块名列表

;;; -------------------------------------------------------------------------
;;; 基础工具
;;; -------------------------------------------------------------------------
(defun bklib:dir ()
  *BKLIB-DIR*)

(defun bklib:slash (s)
  ;; 统一用 "/" 作为路径分隔符
  (setq s (vl-string-translate "\\" "/" s))
  s)

(defun bklib:split (s d / out buf i c)
  ;; 按分隔符 d 拆分字符串
  (setq out '() buf "" i 1)
  (while (<= i (strlen s))
    (setq c (substr s i 1))
    (if (= c d)
        (progn (setq out (cons buf out)) (setq buf ""))
        (setq buf (strcat buf c)))
    (setq i (1+ i)))
  (setq out (cons buf out))
  (reverse out))

(defun bklib:load-manifest ()
  ;; 读取 _manifest.csv -> 每行 (id block file prefix xs ys rot attrs)
  (setq *BKLIB-ROWS* nil)
  (if (setq f (open (strcat (bklib:dir) "/_manifest.csv") "r"))
      (progn
        (while (setq line (read-line f))
          (if (and (/= line "") (/= (substr line 1 1) "#"))
              (progn
                (setq p (bklib:split line ","))
                (if (>= (length p) 8)
                    (setq *BKLIB-ROWS*
                          (cons
                            (list (nth 0 p) (nth 1 p) (nth 2 p) (nth 3 p)
                                  (atof (nth 4 p)) (atof (nth 5 p)) (atof (nth 6 p))
                                  (bklib:split (nth 7 p) ";"))
                            *BKLIB-ROWS*)))))
        (close f)
        (setq *BKLIB-ROWS* (reverse *BKLIB-ROWS*)))
      (princ "\n未找到 _manifest.csv，请用 C:BKPATH 设置库路径。"))
  *BKLIB-ROWS*)

(defun bklib:row (block / r)
  (setq r nil)
  (foreach x *BKLIB-ROWS*
    (if (= (strcase (nth 1 x)) (strcase block))
        (setq r x)))
  r)

(defun bklib:file (block / r f)
  ;; 返回块文件路径：按 <块名>.dwg -> 注册表 FILE 列 -> <块名>.dxf 依次尝试
  (setq r (bklib:row block) f nil)
  (if r
      (progn
        ;; 1) 优先同名 .dwg（用户在 CAD 里维护的正式块）
        (setq f (strcat (bklib:dir) "/blocks/" (nth 1 r) ".dwg"))
        (if (not (findfile f))
            ;; 2) 再看注册表 FILE 列指定的文件名
            (setq f (strcat (bklib:dir) "/blocks/" (nth 2 r))))
        (if (not (findfile f))
            ;; 3) 最后回退同名 .dxf（起步模板）
            (setq f (strcat (bklib:dir) "/blocks/" (nth 1 r) ".dxf"))))
  (if (findfile f) f nil))

;;; -------------------------------------------------------------------------
;;; 属性操作
;;; -------------------------------------------------------------------------
(defun bklib:getattr (e tag / ea ed a)
  (setq ea (entnext e) a nil)
  (while (and ea (setq ed (entget ea)) (= (cdr (assoc 0 ed)) "ATTRIB"))
    (if (= (strcase (cdr (assoc 2 ed))) (strcase tag))
        (setq a (cdr (assoc 1 ed))))
    (setq ea (entnext ea)))
  a)

(defun bklib:setattrs (e alist / ea ed tp val)
  (setq ea (entnext e))
  (while (and ea (setq ed (entget ea)) (= (cdr (assoc 0 ed)) "ATTRIB"))
    (setq tp (strcase (cdr (assoc 2 ed))))
    (if (setq val (assoc tp alist))
        (entmod (subst (cons 1 (cdr val)) (assoc 1 ed) ed)))
    (setq ea (entnext ea)))
  (entupd e)
  (princ))

(defun bklib:num (s prefix / tail)
  ;; 从 "PCS-101" + 前缀 "PCS-" 提取数字 101
  (setq prefix (vl-string-trim " /" prefix))
  (if (and s prefix (vl-string-search prefix s))
      (progn
        (setq tail (substr s (+ 1 (strlen prefix) (vl-string-search prefix s))))
        (setq tail (vl-string-right-trim " " tail))
        (if (distof tail) (fix (distof tail)) 0))
      0))

(defun bklib:nexttag (block prefix / ss i e tag n maxn)
  ;; 扫描图中该块实例的 TAG 属性，取最大编号 +1
  (setq maxn 0)
  (if (setq ss (ssget "X" (list '(0 . "INSERT") (cons 2 block))))
      (progn
        (setq i 0)
        (while (< i (sslength ss))
          (setq e (ssname ss i) i (1+ i))
          (setq tag (bklib:getattr e "TAG"))
          (if tag (setq n (bklib:num tag prefix)))
          (if (and n (> n maxn)) (setq maxn n)))))
  (strcat (vl-string-right-trim " " prefix) (itoa (1+ maxn))))

;;; -------------------------------------------------------------------------
;;; 插入
;;; -------------------------------------------------------------------------
(defun bklib:insert (block pt rot sc / r file e attreq attdia)
  (setq r (bklib:row block) file (bklib:file block))
  (if (not file)
      (progn (princ (strcat "\n库中没有块: " block)) nil)
      (progn
        ;; 插入时抑制属性对话框/提示，稍后程序填充
        (setq attreq (getvar "ATTREQ") attdia (getvar "ATTDIA"))
        (setvar "ATTREQ" 0)
        (setvar "ATTDIA" 0)
        ;; 若块已存在，-INSERT 会询问是否重定义，需应答 Y
        (if (tblsearch "BLOCK" (nth 1 r))
            (command "._-insert" file "Y" pt sc sc rot)
            (command "._-insert" file pt sc sc rot))
        (setvar "ATTREQ" attreq)
        (setvar "ATTDIA" attdia)
        (setq e (entlast))
        e)))

(defun bklib:autotag (e prefix block / tag alist)
  (setq block (if block block (bklib:block-of e)))
  (setq tag (bklib:nexttag (nth 1 (bklib:row block)) prefix))
  (bklib:setattrs e (list (cons "TAG" tag)))
  tag)

(defun bklib:block-of (e / ed)
  (cdr (assoc 2 (entget e))))

;;; -------------------------------------------------------------------------
;;; 对话框选块
;;; -------------------------------------------------------------------------
(defun bklib:pick (/ dclid res names row)
  (setq *BKLIB-NAMES* '())
  (foreach row *BKLIB-ROWS*
    (setq *BKLIB-NAMES* (cons (nth 1 row) *BKLIB-NAMES*)))
  (setq *BKLIB-NAMES* (reverse *BKLIB-NAMES*))
  (setq bklib:pick-result nil)
  (setq dclid (load_dialog (strcat (bklib:dir) "/lisp/bklib.dcl")))
  (if (and dclid (> dclid 0) (new_dialog "bklib_pick" dclid))
      (progn
        (start_list "blist")
        (foreach n *BKLIB-NAMES* (add_list n))
        (end_list)
        (action_tile "blist"
          "(setq idx (atoi (get_tile \"blist\")))")
        (action_tile "accept"
          "(setq bklib:pick-result (nth (atoi (get_tile \"blist\")) *BKLIB-NAMES*)) (done_dialog)")
        (action_tile "cancel" "(done_dialog)")
        (start_dialog)
        (unload_dialog dclid)))
  bklib:pick-result)

;;; -------------------------------------------------------------------------
;;; 命令
;;; -------------------------------------------------------------------------
(defun c:BKINS (/ blk r pt sc rot tag)
  (bklib:load-manifest)
  (if (not *BKLIB-ROWS*) (setq *BKLIB-ROWS* (bklib:load-manifest)))
  (if (not *BKLIB-ROWS*)
      (princ "\n块库未加载，请先 C:BKPATH 设置路径。")
      (progn
        (setq blk (bklib:pick))
        (if blk
            (progn
              (setq r (bklib:row blk))
              (setq pt (getpoint "\n插入点 <Enter=默认0,0>: "))
              (if (not pt) (setq pt '(0 0 0)))
              (setq sc (getreal (strcat "\n缩放比例 <" (rtos (nth 4 r) 2 2) ">: ")))
              (if (not sc) (setq sc (nth 4 r)))
              (setq rot (getreal (strcat "\n旋转角 <" (rtos (nth 6 r) 2 2) ">: ")))
              (if (not rot) (setq rot (nth 6 r)))
              (if (setq e (bklib:insert blk pt rot sc))
                  (progn
                    (setq tag (bklib:autotag e (nth 3 r) blk))
                    (princ (strcat "\n已插入 " blk "  TAG=" tag))))))
          (princ "\n已取消。")))))
  (princ))

(defun c:BKLIST ()
  (bklib:load-manifest)
  (princ "\n块库清单 (ID / 块名 / 前缀 / 属性):")
  (foreach row *BKLIB-ROWS*
    (princ (strcat "\n  " (nth 0 row) " | " (nth 1 row)
                   " | " (nth 3 row) " | " (nth 7 row))))
  (princ))

(defun c:BKREF (/ file blk attreq attdia)
  (bklib:load-manifest)
  (setvar "CMDECHO" 0)
  (setq attreq (getvar "ATTREQ") attdia (getvar "ATTDIA"))
  (setvar "ATTREQ" 0)
  (setvar "ATTDIA" 0)
  (foreach row *BKLIB-ROWS*
    (setq blk (nth 1 row)
          file (bklib:file blk))
    (if file
        (if (tblsearch "BLOCK" blk)
            (progn
              ;; 用 Y 应答“是否重定义”，已有实例将自动更新
              (command "._-insert" file "Y" (list 0 0) 1 1 0)
              (entdel (entlast)))
            (progn
              (command "._-insert" file (list 0 0) 1 1 0)
              (entdel (entlast))))))
  (setvar "ATTREQ" attreq)
  (setvar "ATTDIA" attdia)
  (setvar "CMDECHO" 1)
  (princ "\n已从库重定义全部块。现有块已更新。"))

(defun c:BKPATH (/ p)
  (setq p (getstring t "\n输入块库路径 (例如 C:/.../blocklib): "))
  (if (/= p "")
      (progn
        (setq *BKLIB-DIR* (bklib:slash p))
        (if (setq f (open (strcat (getvar "DWGPREFIX") "bklib_path.txt") "w"))
            (progn (write-line *BKLIB-DIR* f) (close f)))
        (princ (strcat "\n块库路径已设为: " *BKLIB-DIR*))))
  (princ))

;;; -------------------------------------------------------------------------
;;; 一次性：把库中 .dxf 起步模板批量转为 .dwg（在 AutoCAD 里运行，自动跳过已有 .dwg）
;;;   用途：让库里清一色 .dwg，方便你直接打开、往里面加块、保存更新。
;;;   命令： BKDXF2DWG
;;; -------------------------------------------------------------------------
(defun c:BKDXF2DWG (/ acad docs blk r dxf dwg doc)
  (vl-load-com)
  (setq acad (vlax-get-acad-object))
  (setq docs (vla-get-documents acad))
  (bklib:load-manifest)
  (foreach row *BKLIB-ROWS*
    (setq blk (nth 1 row)
          dxf (strcat (bklib:dir) "/blocks/" blk ".dxf")
          dwg (strcat (bklib:dir) "/blocks/" blk ".dwg"))
    (cond
      ((findfile dwg)
       (princ (strcat "\n跳过(已有 .dwg): " blk)))
      ((not (findfile dxf))
       (princ (strcat "\n缺少 .dxf 模板: " blk)))
      (t
       (princ (strcat "\n转换中: " blk))
       (vl-catch-all-apply
         '(lambda ()
            (setq doc (vla-open docs dxf))
            (vla-saveas doc dwg 64)   ; 64 = AutoCAD 2018 DWG
            (princ " -> 完成"))))))
  (princ "\nBKDXF2DWG 完成：库中已无待转换的 .dxf（若有即已生成 .dwg）。"))

;;; 启动时尝试从当前图目录读路径
(if (findfile (strcat (getvar "DWGPREFIX") "bklib_path.txt"))
    (if (setq f (open (strcat (getvar "DWGPREFIX") "bklib_path.txt") "r"))
        (progn (setq *BKLIB-DIR* (read-line f)) (close f))))

(princ "\nbklib.lsp 已加载。命令: BKINS 快速插入, BKLIST 清单, BKREF 刷新库, BKPATH 设路径。")
(princ)
